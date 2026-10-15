#!/usr/bin/env python3
"""
agent-habits hooks. One script, one subcommand per hook event; reads the hook JSON on stdin.

    session-start   SessionStart  prints habits.md (+ which project reminders are active) into the context
    prompt          UserPromptSubmit  context-size warning (to the user and Claude) at each `context_step` past
                                  `context_warn` tokens: every call re-reads the whole context
    pre-tool        PreToolUse    blocks commands matching the project's `block` rules
    post-tool       PostToolUse   runs `check` commands on edited files; notes reminder triggers; flags tool outputs
                                  over `big_output` tokens (they are re-read on every later call)
    stop            Stop          one reminder per triggered rule whose docs weren't edited since

Token limits work without a habits.json; its optional "tokens" object overrides TOKEN_DEFAULTS.

Per-project rules: <project>/.claude/habits.json (schema: examples/habits.example.json, README.md). No file = only
the session-start habits. Exit 2 + stderr = blocked / sent back to Claude. Any internal error exits 0: a broken
config must never block work.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKERS = Path(tempfile.gettempdir()) / "agent_habits"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
READ_TOOLS = {"Read", "Grep", "Glob"}
TOKEN_DEFAULTS = {"context_warn": 200_000, "context_step": 100_000, "big_output": 5_000}
IMAGE_EXT = re.compile(r"\.(png|jpe?g|gif|webp|bmp)$", re.I)


def project_dir(data):
    return Path(data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or ".")


def load_config(data):
    d = Path(os.environ.get("CLAUDE_PROJECT_DIR") or project_dir(data))
    for p in (d / ".claude" / "habits.json", project_dir(data) / ".claude" / "habits.json"):
        if p.is_file():
            cfg = json.loads(p.read_text(encoding="utf-8"))
            cfg["_base"] = d
            return cfg
    return None


def posix(path):
    return str(path).replace("\\", "/")


def any_match(patterns, text):
    return bool(text) and any(re.search(p, text, re.I) for p in patterns or [])


def block(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)


def marker(data, name):
    safe = [re.sub(r"[^\w-]", "_", s) for s in (data.get("session_id", "unknown"), name)]
    return MARKERS / ".".join(safe)


def mark(data, name, what):
    m = marker(data, name)
    if not m.exists():  # keep the first trigger's time: the docs must be edited after it
        MARKERS.mkdir(parents=True, exist_ok=True)
        m.write_text(what[:300], encoding="utf-8")


def docs_mtime(cfg, docs):
    newest = 0.0
    for d in docs:
        p = Path(d) if Path(d).is_absolute() else cfg["_base"] / d
        if p.is_file():
            newest = max(newest, p.stat().st_mtime)
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and ".git" not in f.parts:
                    newest = max(newest, f.stat().st_mtime)
    return newest


def token_limits(cfg):
    return {**TOKEN_DEFAULTS, **((cfg or {}).get("tokens") or {})}


def context_tokens(data):
    """Context size of the session's latest model call, from the transcript's usage fields (None if unknown)."""
    p = data.get("transcript_path")
    if not p or not Path(p).is_file():
        return None
    with open(p, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 4_000_000))
        lines = f.read().decode("utf-8", "ignore").splitlines()
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            u = json.loads(line)["message"]["usage"]
        except (ValueError, KeyError, TypeError):
            continue
        return sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return None


def emit(event, user=None, claude=None):
    out = {}
    if user:
        out["systemMessage"] = user
    if claude:
        out["hookSpecificOutput"] = {"hookEventName": event, "additionalContext": claude}
    if out:
        print(json.dumps(out))


# --- events -------------------------------------------------------------------------------------------------------

def prompt(data, cfg):
    lim = token_limits(cfg)
    ctx = context_tokens(data)
    if ctx is None or ctx < lim["context_warn"]:
        return
    step = (ctx - lim["context_warn"]) // lim["context_step"]
    m = marker(data, "context_step")
    if m.exists() and m.read_text(encoding="utf-8").strip() == str(step):
        return  # already warned at this size
    MARKERS.mkdir(parents=True, exist_ok=True)
    m.write_text(str(step), encoding="utf-8")
    k = ctx // 1000
    emit("UserPromptSubmit",
         user=f"Context is {k}K tokens; every message re-reads all of it. Start a new session for a new topic.",
         claude=f"Context is {k}K tokens and every call re-reads it. If this message starts a new topic or feature, "
                "say so in one line and offer a fresh session (start_session / a handoff brief) before working. "
                "Keep big reads in subagents and avoid opening screenshots unless the answer needs the image.")


def big_output(data, cfg):
    """Nudge after a tool output that will be re-read on every later call."""
    if data.get("agent_id"):
        return  # inside a subagent: big reads are what it is for
    tool, inp = data.get("tool_name", ""), data.get("tool_input", {})
    lim = token_limits(cfg)
    path = posix(inp.get("file_path") or "")
    if tool == "Read" and IMAGE_EXT.search(path):
        ctx = context_tokens(data) or 0
        if ctx >= lim["context_warn"]:
            emit("PostToolUse", claude=f"Image read at {ctx // 1000}K context: it rides along on every later call. "
                                       "Prefer numbers from logs; crop or downscale images and open each one once.")
        return
    size = len(json.dumps(data.get("tool_response", ""), ensure_ascii=False)) // 4
    if size >= lim["big_output"]:
        emit("PostToolUse", claude=f"That {tool} output was ~{size // 1000}K tokens and is re-read on every later call. "
                                   "Next time filter it (grep, head, a narrower range) or hand the read to a subagent "
                                   "(agent-habits:source-lookup / log-triage) that returns only the answer.")


def session_start(data):
    print((ROOT / "habits.md").read_text(encoding="utf-8").strip())
    cfg = load_config(data)
    if cfg:
        names = [r.get("name", "?") for r in cfg.get("reminders", [])]
        if names:
            print(f"Project reminders active (.claude/habits.json): {', '.join(names)}.")


def pre_tool(data, cfg):
    tool, inp = data.get("tool_name", ""), data.get("tool_input", {})
    cmd = inp.get("command", "")
    for rule in cfg.get("block", []):
        if tool not in rule.get("tools", ["Bash"]):
            continue
        if re.search(rule["pattern"], cmd) and not (rule.get("unless") and re.search(rule["unless"], cmd)):
            block(rule["message"])


def post_tool(data, cfg):
    tool, inp = data.get("tool_name", ""), data.get("tool_input", {})
    path = posix(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
    cmd = inp.get("command", "")

    for r in cfg.get("reminders", []):
        name = r.get("name", "reminder")
        if tool in EDIT_TOOLS and any_match(r.get("on_edit"), path):
            mark(data, name, path)
        elif tool in SHELL_TOOLS and any_match(r.get("on_command"), cmd):
            mark(data, name, cmd)
        elif tool in READ_TOOLS and any_match(r.get("on_read"), path):
            mark(data, name, path)
        elif tool == "WebFetch" and any_match(r.get("on_url"), inp.get("url", "")):
            mark(data, name, inp.get("url", ""))
        elif tool == "WebSearch" and any_match(r.get("on_search"), inp.get("query", "")):
            mark(data, name, inp.get("query", ""))

    if tool in EDIT_TOOLS and path:
        for chk in cfg.get("check", []):
            if not re.search(chk["path"], path, re.I):
                continue
            argv = [a.replace("{file}", path) for a in chk["command"]]
            exe = argv[0] if Path(argv[0]).exists() else shutil.which(argv[0])
            if not exe:
                continue  # tool not installed on this machine: skip silently
            r = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, timeout=chk.get("timeout", 60))
            if r.returncode != 0:
                out = (r.stderr or r.stdout).strip()[-2000:]
                block(f"{chk.get('message', 'Check failed')} ({' '.join(argv)}):\n{out}")


def stop(data, cfg):
    if data.get("stop_hook_active"):
        return  # already reminded once this turn
    due = []
    for r in cfg.get("reminders", []):
        m = marker(data, r.get("name", "reminder"))
        if not m.exists():
            continue
        if docs_mtime(cfg, r.get("docs", [])) <= m.stat().st_mtime:
            due.append(r["message"])
        m.unlink()  # one reminder per batch of triggers
    if due:
        block("\n\n".join(due) + "\n\n(Nothing to record? Answer in one short line.)")


def main():
    event = sys.argv[1]
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    if event == "session-start":
        session_start(data)
        return
    try:
        cfg = load_config(data)
    except ValueError:
        cfg = None  # broken habits.json: token hooks still run with defaults
    if event == "prompt":
        prompt(data, cfg)
        return
    if cfg:
        {"pre-tool": pre_tool, "post-tool": post_tool, "stop": stop}[event](data, cfg)
    if event == "post-tool":
        big_output(data, cfg)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # never block work because of a hook bug
        print(f"agent-habits hook error ({sys.argv[1:]}): {e}", file=sys.stderr)
        sys.exit(0)
