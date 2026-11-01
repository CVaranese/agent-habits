#!/usr/bin/env python3
"""
agent-habits hooks. One script, one subcommand per hook event; reads the hook JSON on stdin.

    session-start   SessionStart  prints habits.md (+ which project reminders are active) into the context
    prompt          UserPromptSubmit  context-size warning (to the user and Claude) at each `context_step` past
                                  `context_warn` tokens: every call re-reads the whole context
    pre-tool        PreToolUse    blocks commands matching the project's `block` rules; built-in guard: shell file
                                  writes through heredocs / multi-line echo / Set-Content, inline Python that opens a
                                  file for writing and sed -i / perl -i on .md, into non-temp paths, are
                                  blocked (use Write/Edit); edits / shell writes to `knowledge_paths` are blocked
                                  unless the call comes from the librarian agent, to `trackers.paths` unless from
                                  the scheduler agent; `experiments` reminder (once per
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
# files the control-character guard skips; a project adds its own formats with "guards": {"binary_ext": [...]}
BINARY_EXT = re.compile(r"\.(png|jpe?g|gif|webp|bmp|ico|pdf|zip|gz|tar|7z|exe|dll|so|dylib|pyc|class|jar|bin|dat"
                        r"|wav|mp3|ogg|mp4|mov|ttf|otf|woff2?)$", re.I)

# --- shell write guard: heredoc / multi-line echo / PowerShell here-string that lands in a file ---------------------
_Q = r'"[^"]*"|\'[^\']*\''  # a quoted shell word
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(?:" + _Q + r"|[A-Za-z_][\w-]*)")  # not a `<<<` here-string
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
# inline Python (heredoc fed to `python -`, `python -c`) that opens a file for writing: read-modify-write scripts have
# truncated and garbled files the Edit tool would have changed cleanly
PY_RUN = re.compile(r"(?:^|[;&|(]|\n)\s*(?:\w+=\S*\s+)*(?:\S*[/\\])?(?:python[\d.]*|py)(?:\.exe)?\s", re.I)
PY_OPEN_W = re.compile(r"\bopen\(\s*([^,()]+?)\s*,\s*(?:mode\s*=\s*)?(['\"])[rbt]*[wax+][^'\"]*\2")
PY_WRITE_TEXT = re.compile(r"(?:Path\(\s*([^()]+?)\s*\)|\b(\w+))\.write_(?:text|bytes)\(")
PY_PATH_LIT = re.compile(r"(['\"])([^'\"\n]*[/\\][^'\"\n]*\.\w{1,5}|[^'\"\n/\\ ]+\.(?:md|py|lua|json|txt|csv|cs|ini|ya?ml|toml))\1")
# `sed -i` / `perl -i` on a markdown file: the Edit tool's job
INPLACE_MD = re.compile(r"(?:^|[;&|(]|\n)\s*(?:sed|perl)\b[^\n;&|]*?\s-i\S*\s[^\n;&|]*?(['\"]?)([^\s'\";&|]+\.md)\1"
                        r"(?=\s|$|[;&|])", re.I)

# --- knowledge guard: only the librarian agent writes the paths in `knowledge_paths` ---------------------------------
LIBRARIANS = {"agent-habits:librarian", "librarian"}   # plugin agent / a copy in .claude/agents
SCHEDULERS = {"agent-habits:scheduler", "scheduler"}   # same rule for `trackers.paths`
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
            cfg["_base"] = p.parent.parent  # the project that holds this habits.json
            derive_trackers(cfg)
            return cfg
    return None


def derive_trackers(cfg):
    """With a "tasks" config, fill a missing trackers.map (the task index) and trackers.paths (index, plan, done list;
    not the task files) from it, with tasks.py's defaults. Explicit trackers values win."""
    if not isinstance(cfg.get("tasks"), dict):
        return
    t = {"dir": "docs/tasks", "plan": "PLAN.md", "index": "README.md", "done": "done", **cfg["tasks"]}
    d = t["dir"].strip("/")
    tr = cfg.setdefault("trackers", {})
    tr.setdefault("map", f"{d}/{t['index']}")
    files = [t["index"], t["plan"], f"{t['done']}/{t['index']}"]
    tr.setdefault("paths", ["/" + re.escape(f"{d}/{f}") + "$" for f in files if f])


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


def py_value(expr, body):
    """A file-name expression in inline Python as a string: a literal, or a name assigned a literal (`p = '...'`,
    `p = Path('...')`) in the same script. None when it can't be told (f-string, joined, computed)."""
    expr = expr.strip()
    m = re.fullmatch(r"[rR]?(['\"])([^'\"]*)\1", expr)
    if m:
        return m.group(2)
    if re.fullmatch(r"\w+", expr):
        found = re.findall(r"(?:^|[;\n])\s*" + expr + r"\s*=\s*(?:Path\(\s*)?[rR]?(['\"])([^'\"\n]*)\1", body)
        if found:
            return found[-1][1]
    return None


def native_path(p):
    """Git Bash `/d/x` -> `D:/x`, so Python and git.exe can use it."""
    return re.sub(r"^/([a-zA-Z])/", lambda m: m.group(1).upper() + ":/", p) if os.name == "nt" else p


def shell_context(cmd, cwd):
    """(shell variables set in the command, the folder its last `cd` goes to): `S=<scratchpad>; python - ...` writing
    `$S/x.py`, or `cd <scratchpad> && python - ...` writing `x.py`, is a temp write."""
    words = without_bodies("Bash", cmd)
    env = {k: v.strip("'\"") for k, v in re.findall(r"(?:^|[;&|\s])(\w+)=(" + _Q + r"|[^\s;&|]+)", words)}
    expand = lambda t: re.sub(r"\$\{?(\w+)\}?", lambda m: env.get(m.group(1), m.group(0)), t)
    base, in_temp = str(cwd), False   # the session's own folder is the project: only a `cd` named temp counts
    for m in re.finditer(r"(?:mkdir\s+-p\s+(" + _Q + r"|[^\s;&|]+)[^\n;&|]*[;&|]+\s*)?\bcd\s+(" + _Q + r"|[^\s;&|]+)",
                         words):
        target = m.group(2).strip("'\"")
        if target == "$_" and m.group(1):
            target = m.group(1).strip("'\"")
        target = native_path(expand(target))
        absolute = os.path.isabs(target) or re.match(r"^[A-Za-z]:", target)
        in_temp = (not source_targets([target])) or (in_temp and not absolute)
        base = target if absolute else os.path.join(base, target)
    return expand, base, in_temp


def is_source_write(target, ctx):
    """A write to a source file: not a temp path (after expanding the command's variables and its `cd`) and not
    git-ignored (`out/`, build output)."""
    expand, base, in_temp = ctx
    t = native_path(expand(target.strip()))
    absolute = os.path.isabs(t) or re.match(r"^[A-Za-z]:", t)
    if not source_targets([t]) or (in_temp and not absolute):
        return False
    full = t if absolute else posix(os.path.join(base, t))
    folder = Path(full).parent
    while not folder.is_dir() and folder != folder.parent:   # git -C needs an existing folder
        folder = folder.parent
    try:   # 0 = ignored; 1 = not ignored; 128 = no repo there (a source write all the same)
        r = subprocess.run(["git", "-C", str(folder), "check-ignore", "-q", full], capture_output=True, timeout=5)
        return r.returncode != 0
    except (OSError, subprocess.SubprocessError):
        return True


def inline_write_target(tool, cmd, cwd="."):
    """The source file inline Python opens for writing, or a markdown file `sed -i` / `perl -i` edits, or None.
    Scripts run from a file (`python tools/x.py`) aren't looked into; temp and git-ignored targets pass."""
    if tool != "Bash" or not cmd:
        return None
    ctx = shell_context(cmd, cwd)
    for m in INPLACE_MD.finditer(cmd):
        if is_source_write(m.group(2), ctx):
            return m.group(2)
    if not PY_RUN.search(cmd):
        return None
    exprs = [m.group(1) for m in PY_OPEN_W.finditer(cmd)]
    exprs += [m.group(1) or m.group(2) for m in PY_WRITE_TEXT.finditer(cmd)]
    unknown = False
    for e in exprs:
        v = py_value(e, cmd)
        if v is None:
            unknown = True
        elif is_source_write(v, ctx):
            return v
    if unknown:   # target computed: any source file path the script names is the likely target
        for m in PY_PATH_LIT.finditer(cmd):
            if is_source_write(m.group(2), ctx):
                return m.group(2)
    return None


def control_char_report(path, binary_ext=()):
    """Problems in a just-edited text file: control bytes, invalid UTF-8, mixed line endings. None if clean.
    binary_ext: the project's extra binary extensions (habits.json guards.binary_ext), skipped like BINARY_EXT."""
    p = Path(path)
    extra = tuple("." + str(e).lower().lstrip(".") for e in binary_ext or ())
    if not p.is_file() or BINARY_EXT.search(path) or (extra and path.lower().endswith(extra)) \
            or p.stat().st_size > 8_000_000:
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
    """Block writes to the knowledge base (cfg["knowledge_paths"]) from anyone but the librarian agent, and to the
    work trackers (cfg["trackers"]["paths"]) from anyone but the scheduler agent."""
    cfg = cfg or {}
    owned = [(cfg.get("knowledge_paths"), LIBRARIANS,
              "is in the knowledge base (habits.json knowledge_paths), which only the librarian agent writes. Collect "
              "the findings (fact, evidence path, status tested / researched / guess) and hand them over in one call "
              "at the end of the work: Agent tool, subagent_type agent-habits:librarian, prompt starting with "
              "\"File:\". It picks the entry, merges instead of duplicating and links both ways."),
             ((cfg.get("trackers") or {}).get("paths"), SCHEDULERS,
              "is a work tracker (habits.json trackers.paths: task index, plan), which only the scheduler agent "
              "writes. Hand it over in one call: Agent tool, subagent_type agent-habits:scheduler, prompt starting "
              "with \"Queue:\" (new work found: what, why, evidence, files, what the user must answer first) or "
              "\"Close:\" (task id, outcome, commits per repo, evidence, review findings). The task file's own "
              "headers and Progress notes stay yours to write; the generated board blocks are rewritten by the "
              "project's tasks.py.")]
    for paths, owners, why in owned:
        if not paths or data.get("agent_type") in owners:
            continue
        hits = [t for t in knowledge_targets(data, tool, inp) if any_match(paths, t)]
        if hits:
            block(f"{hits[0]} {why}")


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

# blocks the harness sends for background work, not typed by the user: a warning on a prompt made only of these landed
# mid-task and led to a hand-over in the middle of a step (2026-10-31, a long test run's task-notification at 203K).
# The user's own text can arrive after such a block in the same prompt: that prompt still warns
NOT_USER = re.compile(r"<(system-reminder|task-notification|cross-session-message|agent-message|side-sessions-event|"
                      r"ci-monitor-event)\b.*?</\1>", re.S)


def prompt(data, cfg):
    lim = token_limits(cfg)
    ctx = context_tokens(data)
    if ctx is None or ctx < lim["context_warn"]:
        return
    text = str(data.get("prompt") or "")
    if text.strip() and not NOT_USER.sub("", text).strip():
        return  # nothing typed: the next prompt the user types gets it (the step marker isn't written)
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
                "say so in one line and offer a fresh start before working. Mid-task, don't stop in the middle of a "
                "step: at the next phase boundary (a step finished, verified and committed), offer to hand the rest "
                "off as a written brief (the project's task file, or one copyable block) the user starts "
                "themselves, or `/compact <focus>` to stay here (the only option on mobile). Keep big reads in "
                "subagents and avoid opening screenshots unless the answer needs the image.")


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
        target = inline_write_target(tool, cmd, project_dir(data))
        if target:
            block(f"Inline write into {target} blocked: inline Python that rewrites a file, or sed -i / perl -i on a "
                  "markdown file, has truncated and garbled files before. Use the Edit tool (Write for a new file). "
                  "Scripts run from a file (python tools/x.py) and writes to temp paths (tmp, temp, the scratchpad) "
                  "still work.")
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
        report = control_char_report(path, guard_flags(cfg).get("binary_ext"))
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
