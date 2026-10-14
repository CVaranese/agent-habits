#!/usr/bin/env python3
"""
agent-habits hooks. One script, one subcommand per hook event; reads the hook JSON on stdin.

    session-start   SessionStart  prints habits.md (+ which project reminders are active) into the context
    pre-tool        PreToolUse    blocks commands matching the project's `block` rules
    post-tool       PostToolUse   runs `check` commands on edited files; notes reminder triggers
    stop            Stop          one reminder per triggered rule whose docs weren't edited since

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


# --- events -------------------------------------------------------------------------------------------------------

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
    cfg = load_config(data)
    if cfg:
        {"pre-tool": pre_tool, "post-tool": post_tool, "stop": stop}[event](data, cfg)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # never block work because of a hook bug
        print(f"agent-habits hook error ({sys.argv[1:]}): {e}", file=sys.stderr)
        sys.exit(0)
