#!/usr/bin/env python3
"""
agent-habits hooks. One script, one subcommand per hook event; reads the hook JSON on stdin.

    session-start   SessionStart  prints habits.md (+ which project reminders are active) into the context
    prompt          UserPromptSubmit  context-size warning (to the user and Claude) at each `context_step` past
                                  `context_warn` tokens: every call re-reads the whole context
    pre-tool        PreToolUse    blocks commands matching the project's `block` rules; built-in guard: shell file
                                  writes through heredocs / multi-line echo / Set-Content into non-temp paths are
                                  blocked (use Write/Edit); edits / shell writes to `knowledge_paths` are blocked
                                  unless the call comes from the librarian agent; `experiments` reminder (once per
                                  session) when a
                                  long-run command starts and no plan file was written this session
    post-tool       PostToolUse   runs `check` commands on edited files; built-in guard: control bytes (NUL, form
                                  feed...), invalid UTF-8 and mixed line endings in the edited file are reported;
                                  notes reminder triggers; flags tool outputs over `big_output` tokens (they are
                                  re-read on every later call)
    stop            Stop          one reminder per triggered rule whose docs weren't edited since
                                  (once per session for rules with "once_per_session": true)

Token limits and the shell/file guards work without a habits.json; its optional "tokens" / "guards" objects override
TOKEN_DEFAULTS / GUARD_DEFAULTS.

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
GUARD_DEFAULTS = {"shell_writes": True, "control_chars": True}
IMAGE_EXT = re.compile(r"\.(png|jpe?g|gif|webp|bmp)$", re.I)
BINARY_EXT = re.compile(r"\.(png|jpe?g|gif|webp|bmp|ico|pdf|zip|gz|7z|exe|dll|so|pyc|pac|brsar|dat|bin|wav|mp3|ogg"
                        r"|ttf|otf|woff2?|uasset|umap|fbx|sav|raw)$", re.I)

# --- shell write guard: heredoc / multi-line echo / PowerShell here-string that lands in a file ---------------------
_Q = r'"[^"]*"|\'[^\']*\''  # a quoted shell word
HEREDOC = re.compile(r"<<-?\s*(?:" + _Q + r"|[A-Za-z_][\w-]*)")
PS_HERESTRING = re.compile(r"@[\"']\s*\r?\n")
# `> file`, `>> file`, `&> file`; not `2>&1`, `>&2`, `<<`
REDIRECT = re.compile(r"(?:(?<![<>&\d])>{1,2}|&>)(?!&)\s*(" + _Q + r"|[^\s|;&<>]+)")
TEE = re.compile(r"\btee\s+(?:-\w+\s+)*(" + _Q + r"|[^\s|;&<>]+)")
ECHO_WRITE = re.compile(r"\b(?:echo|printf)\s+((?:" + _Q + r"|[^\s>;|&])+)\s*>{1,2}(?!&)\s*(" + _Q + r"|[^\s|;&<>]+)",
                        re.S)
PS_WRITER = re.compile(r"\b(Set-Content|Add-Content|Out-File)\b", re.I)
PS_TARGET = re.compile(r"-(?:Path|LiteralPath|FilePath)\s+(" + _Q + r"|\S+)|WriteAll(?:Text|Lines|Bytes)\(\s*(" + _Q + r")",
                       re.I)
PS_CMDLET_ARGS = re.compile(r"\b(?:Set-Content|Add-Content|Out-File)\b([^|;\n]*)", re.I)

# --- knowledge guard: only the librarian agent writes the paths in `knowledge_paths` ---------------------------------
LIBRARIANS = {"agent-habits:librarian", "librarian"}   # plugin agent / a copy in .claude/agents
# shell commands that change the files they name: every path argument counts, except cp / Copy-Item (destination only)
SHELL_CHANGERS = re.compile(r"^\s*(?:\w+=\S*\s+)*(?:sudo\s+)?(?:(sed|perl)\b(?=[^\n]*\s-i)"
                            r"|(mv|rm|git\s+(?:mv|rm|checkout|restore)|Remove-Item|Move-Item|Clear-Content)\b"
                            r"|(cp|Copy-Item)\b)", re.I)
PS_FLAG = re.compile(r"-\w+(\s+(?!-)(?:" + _Q + r"|\S+))?")  # `-Encoding utf8`, `-Force`
# targets that are not source files: temp dirs, the scratchpad, devices, stdout
TEMP_TARGET = re.compile(r"(^|[/\\\"'=:])(tmp|temp|scratchpad?)([/\\\"']|$)|\$\{?(TMP|TEMP|TMPDIR|SCRATCH)|%TE?MP%"
                         r"|AppData[/\\]Local[/\\]Temp|^[\"']?(-|/dev/\w+|NUL)[\"']?$", re.I)
CONTROL_RE = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")   # all but tab, LF, CR
CONTROL_NAMES = {0: "NUL", 0x0B: "VT", 0x0C: "form feed", 0x1B: "ESC", 0x7F: "DEL"}


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


def guard_flags(cfg):
    return {**GUARD_DEFAULTS, **((cfg or {}).get("guards") or {})}


def source_targets(targets):
    return [t for t in targets if t and not TEMP_TARGET.search(t.strip())]


def without_bodies(tool, cmd):
    """The command with heredoc / here-string bodies left out, so only the shell's own words are searched."""
    if tool == "PowerShell":
        return re.sub(r"@(['\"])\r?\n.*?\r?\n\1@", "@''@", cmd, flags=re.S)
    out, ends = [], []
    for ln in cmd.split("\n"):
        if ends:
            if ln.strip() == ends[0]:
                ends.pop(0)
            continue
        out.append(ln)
        ends = [m.group(0).lstrip("<-").strip().strip("'\"") for m in HEREDOC.finditer(ln)]
    return "\n".join(out)


def shell_write_target(tool, cmd):
    """The non-temp file a shell command writes multi-line text into (heredoc, multi-line echo/printf, PowerShell
    here-string / Set-Content), or None. A heredoc that only feeds a program (`git commit -F - <<EOF`) is fine."""
    if not cmd:
        return None
    if tool == "PowerShell":
        if not (PS_HERESTRING.search(cmd) or PS_WRITER.search(cmd)):
            return None
        cmd = without_bodies(tool, cmd)
        found = ["".join(m.groups("")) for m in PS_TARGET.finditer(cmd)]
        for m in PS_CMDLET_ARGS.finditer(cmd):  # positional path: the first word left after `-Flag value` pairs
            found += re.findall(_Q + r"|\S+", PS_FLAG.sub(" ", m.group(1)))[:1]
        found += [m.group(1) for m in REDIRECT.finditer(cmd)]
        if not found:
            return "<unknown target>" if PS_WRITER.search(cmd) else None
        hits = source_targets(found)
        return hits[0] if hits else None
    if HEREDOC.search(cmd):
        cmd = without_bodies(tool, cmd)   # a `>` in the fed text (commit message, python) is no redirect
        hits = source_targets([m.group(1) for m in REDIRECT.finditer(cmd)] + [m.group(1) for m in TEE.finditer(cmd)])
        if hits:
            return hits[0]
    for m in ECHO_WRITE.finditer(cmd):
        arg, target = m.group(1), m.group(2)
        if ("\n" in arg or arg.count("\\n") >= 2) and source_targets([target]):
            return target
    return None


def control_char_report(path):
    """Problems in a just-edited text file: control bytes, invalid UTF-8, mixed line endings. None if clean."""
    p = Path(path)
    if not p.is_file() or BINARY_EXT.search(path) or p.stat().st_size > 8_000_000:
        return None
    raw = p.read_bytes()
    nl = b"\n"

    def line(i):
        return raw.count(nl, 0, i) + 1

    problems = []
    hits = [(m.start(), raw[m.start()]) for m in CONTROL_RE.finditer(raw)]
    if hits:
        shown = ", ".join("%s at line %d" % (CONTROL_NAMES.get(b, "0x%02X" % b), line(i)) for i, b in hits[:5])
        problems.append("%d control byte(s): %s" % (len(hits), shown))
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as e:
        problems.append("not valid UTF-8 (byte 0x%02X at line %d; a cp1252 write?)" % (raw[e.start], line(e.start)))
    crlf = raw.count(b"\r\n")
    lf = raw.count(nl) - crlf
    if crlf and lf:
        problems.append(f"mixed line endings ({crlf} CRLF, {lf} LF): the edit changed the file's ending style")
    return "; ".join(problems) if problems else None


def knowledge_targets(data, tool, inp):
    """Files this tool call would change, as absolute forward-slash paths (relative ones resolved against cwd)."""
    if tool in EDIT_TOOLS:
        found = [inp.get("file_path") or inp.get("notebook_path") or ""]
    elif tool in SHELL_TOOLS:
        cmd = without_bodies(tool, inp.get("command", ""))
        found = [m.group(1) for m in REDIRECT.finditer(cmd)] + [m.group(1) for m in TEE.finditer(cmd)]
        found += ["".join(m.groups("")) for m in PS_TARGET.finditer(cmd)]
        for m in PS_CMDLET_ARGS.finditer(cmd):   # Set-Content's path, not the value it writes
            found += re.findall(_Q + r"|\S+", PS_FLAG.sub(" ", m.group(1)))[:1]
        for seg in re.split(r"[|;&\n]+", cmd):
            m = SHELL_CHANGERS.match(seg)
            if not m:
                continue
            words = [w for w in re.findall(_Q + r"|\S+", seg[m.end():]) if not w.startswith("-")]
            found += words[-1:] if m.group(3) else words
    else:
        return []
    out = []
    for f in found:
        f = f.strip().strip("\"'")
        if not f or (tool in SHELL_TOOLS and TEMP_TARGET.search(f)):
            continue
        p = Path(os.path.expandvars(os.path.expanduser(f)))
        out.append(posix(p if p.is_absolute() else project_dir(data) / p) + ("/" if f.endswith(("/", "\\")) else ""))
    return out


def knowledge_guard(data, cfg, tool, inp):
    """Block writes to the knowledge base (cfg["knowledge_paths"]) from anyone but the librarian agent."""
    paths = (cfg or {}).get("knowledge_paths")
    if not paths or data.get("agent_type") in LIBRARIANS:
        return
    hits = [t for t in knowledge_targets(data, tool, inp) if any_match(paths, t)]
    if hits:
        block(f"{hits[0]} is in the knowledge base (habits.json knowledge_paths), which only the librarian agent "
              "writes. Collect the findings (fact, evidence path, status tested / researched / guess) and hand them "
              "over in one call at the end of the work: Agent tool, subagent_type agent-habits:librarian, prompt "
              "starting with \"File:\". It picks the entry, merges instead of duplicating and links both ways.")


def experiments_reminder(data, cfg, tool, cmd):
    """Once per session: a long-run command starts (cfg["experiments"]["on_command"]) with no plan file edited
    this session (cfg["experiments"]["plan"] path patterns, noted by post_tool)."""
    ex = (cfg or {}).get("experiments")
    if not ex or tool not in SHELL_TOOLS or not any_match(ex.get("on_command"), cmd):
        return
    if marker(data, "experiment.plan").exists() or marker(data, "experiment.reminded").exists():
        return
    MARKERS.mkdir(parents=True, exist_ok=True)
    marker(data, "experiment.reminded").write_text(cmd[:300], encoding="utf-8")
    msg = ex.get("message") or ("This command starts a long scripted experiment and no experiment plan was written "
                                "this session. The experiment-plan skill (agent-habits) writes one: goal, assumptions "
                                "tagged verified / needs-user / unknown, prerequisites checked on disk, the cheapest "
                                "disproving run, run budget; anything tagged needs-user stops for the user first.")
    emit("PreToolUse", user="Long run started without an experiment plan (reminder sent to Claude, once per session).",
         claude=msg)


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
    if tool in EDIT_TOOLS:
        return  # the hook's tool_response carries the whole file; the model only sees a one-line result
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
    if guard_flags(cfg)["shell_writes"] and tool in SHELL_TOOLS:
        target = shell_write_target(tool, cmd)
        if target:
            block(f"Shell write into {target} blocked: multi-line text through heredocs / echo / Set-Content has "
                  "damaged files before (NUL bytes, form feeds, CRLF/LF mixes, cp1252). Use the Write or Edit tool "
                  "for files; a heredoc that only feeds a program (git commit -F -, python -) still works. "
                  "Temp paths (tmp, temp, the scratchpad) are exempt.")
    knowledge_guard(data, cfg, tool, inp)
    for rule in (cfg or {}).get("block", []):
        if tool not in rule.get("tools", ["Bash"]):
            continue
        if re.search(rule["pattern"], cmd) and not (rule.get("unless") and re.search(rule["unless"], cmd)):
            block(rule["message"])
    experiments_reminder(data, cfg, tool, cmd)


def post_tool(data, cfg):
    tool, inp = data.get("tool_name", ""), data.get("tool_input", {})
    path = posix(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
    cmd = inp.get("command", "")

    ex = (cfg or {}).get("experiments") or {}
    if tool in EDIT_TOOLS and any_match(ex.get("plan"), path):   # before the guard: its block exits
        mark(data, "experiment.plan", path)
    if tool in EDIT_TOOLS and path and guard_flags(cfg)["control_chars"]:
        report = control_char_report(path)
        if report:
            block(f"Edited file {path} has {report}. Fix it with the Edit tool (or rewrite with Write) before "
                  "moving on.")
    if not cfg:
        return

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
        name = r.get("name", "reminder")
        m = marker(data, name)
        if not m.exists():
            continue
        done = marker(data, name + ".reminded")
        if docs_mtime(cfg, r.get("docs", [])) <= m.stat().st_mtime and not (r.get("once_per_session") and done.exists()):
            due.append(r["message"])
            if r.get("to_librarian"):
                due[-1] += (" Knowledge-base entries are written by the librarian agent (agent-habits:librarian): "
                            "hand it all of this session's findings in one \"File:\" call (fact, evidence, status).")
            if r.get("once_per_session"):
                done.write_text("1", encoding="utf-8")  # each blocked stop costs a full extra turn
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
    if event in ("pre-tool", "post-tool"):  # guards run without a config
        {"pre-tool": pre_tool, "post-tool": post_tool}[event](data, cfg)
    elif cfg:
        stop(data, cfg)
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
