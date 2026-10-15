#!/usr/bin/env python3
"""Self-test for habits.py: runs each hook on sample inputs in a temp project. Exit 0 = all passed."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HABITS = Path(__file__).resolve().parent / "habits.py"


def run(event, data, project):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project))
    r = subprocess.run([sys.executable, "-I", str(HABITS), event], input=json.dumps(data), text=True,
                       capture_output=True, env=env)
    return r.returncode, r.stdout + r.stderr


def main():
    fails = []

    def expect(label, got, want):
        print(f"{'ok  ' if got == want else 'FAIL'} {label}: exit {got}" + ("" if got == want else f" (want {want})"))
        if got != want:
            fails.append(label)

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        (proj / ".claude").mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "CHANGELOG.md").write_text("x")
        (proj / ".claude" / "habits.json").write_text(json.dumps({
            "block": [{"tools": ["Bash"], "pattern": r"deploy\b", "unless": r"--dry-run", "message": "dry run first"}],
            "check": [{"path": r"\.py$", "command": [sys.executable, "-m", "py_compile", "{file}"],
                       "message": "syntax"}],
            "reminders": [{"name": "changelog", "on_edit": [r"/src/"], "docs": ["docs/CHANGELOG.md"],
                           "message": "add a changelog line"},
                          {"name": "notes", "on_url": [r"^https://example\.org/"], "on_search": ["widget"],
                           "docs": ["docs"], "message": "record findings"}],
        }))
        good, bad = proj / "src" / "ok.py", proj / "src" / "bad.py"
        good.parent.mkdir()
        good.write_text("x = 1\n")
        bad.write_text("x = \n")
        s = {"session_id": f"selftest{os.getpid()}", "cwd": str(proj)}

        code, out = run("session-start", s, proj)
        expect("session-start prints habits + reminders", code == 0 and "Project reminders active" in out, True)
        expect("block deploy", run("pre-tool", {**s, "tool_name": "Bash", "tool_input": {"command": "deploy prod"}},
                                   proj)[0], 2)
        expect("allow deploy --dry-run", run("pre-tool", {**s, "tool_name": "Bash",
                                                         "tool_input": {"command": "deploy --dry-run"}}, proj)[0], 0)
        expect("check fails on bad file", run("post-tool", {**s, "tool_name": "Write",
                                                           "tool_input": {"file_path": str(bad)}}, proj)[0], 2)
        expect("check passes on good file", run("post-tool", {**s, "tool_name": "Edit",
                                                             "tool_input": {"file_path": str(good)}}, proj)[0], 0)
        # good.py edit under /src/ triggered "changelog"; docs untouched -> reminder once, then quiet
        expect("stop reminds", run("stop", s, proj)[0], 2)
        expect("stop quiet after reminding", run("stop", s, proj)[0], 0)
        # web research that is then recorded -> no reminder
        run("post-tool", {**s, "tool_name": "WebFetch", "tool_input": {"url": "https://example.org/spec"}}, proj)
        time.sleep(0.05)
        (proj / "docs" / "notes.md").write_text("finding")
        expect("no reminder after docs edit", run("stop", s, proj)[0], 0)
        # unrelated web/search -> nothing
        run("post-tool", {**s, "tool_name": "WebFetch", "tool_input": {"url": "https://other.com/"}}, proj)
        run("post-tool", {**s, "tool_name": "WebSearch", "tool_input": {"query": "gadget prices"}}, proj)
        expect("unrelated research ignored", run("stop", s, proj)[0], 0)
        expect("stop_hook_active skips", run("stop", {**s, "stop_hook_active": True}, proj)[0], 0)
        # token hooks: context warning once per step, big-output nudge, small output quiet
        tr = proj / "t.jsonl"

        def ctx(n):
            u = {"input_tokens": 10, "cache_read_input_tokens": n, "cache_creation_input_tokens": 0}
            tr.write_text(json.dumps({"type": "assistant", "message": {"usage": u}}) + "\n")
        t = {**s, "transcript_path": str(tr)}
        ctx(50_000)
        expect("small context quiet", run("prompt", t, proj)[1].strip(), "")
        ctx(250_000)
        out = run("prompt", t, proj)[1]
        expect("big context warns", "systemMessage" in out and "250K" in out, True)
        expect("same step quiet", run("prompt", t, proj)[1].strip(), "")
        ctx(320_000)
        expect("next step warns again", "320K" in run("prompt", t, proj)[1], True)
        big = {**t, "tool_name": "Bash", "tool_input": {"command": "cat big"}, "tool_response": {"stdout": "x" * 40_000}}
        expect("big output nudged", "additionalContext" in run("post-tool", big, proj)[1], True)
        expect("subagent big output quiet", run("post-tool", {**big, "agent_id": "a1"}, proj)[1].strip(), "")
        small = {**big, "tool_response": {"stdout": "ok"}}
        expect("small output quiet", run("post-tool", small, proj)[1].strip(), "")
        img = {**t, "tool_name": "Read", "tool_input": {"file_path": "C:/x/shot.png"}, "tool_response": {}}
        expect("image at big context nudged", "Image read" in run("post-tool", img, proj)[1], True)
        # broken config never blocks
        (proj / ".claude" / "habits.json").write_text("{ not json")
        expect("broken config exits 0", run("pre-tool", {**s, "tool_name": "Bash",
                                                        "tool_input": {"command": "deploy prod"}}, proj)[0], 0)

    print("all passed" if not fails else f"{len(fails)} failed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
