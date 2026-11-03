#!/usr/bin/env python3
"""Self-test for habits.py: runs each hook on sample inputs in a temp project. Exit 0 = all passed."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HABITS = Path(__file__).resolve().parent / "habits.py"


USER_DIR = Path(tempfile.mkdtemp())  # stands in for ~/.claude, so the real user habits.json doesn't leak in


def run(event, data, project):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project), CLAUDE_CONFIG_DIR=str(USER_DIR))
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
        expect("habit ids stripped", "[evidence]" not in out and "- Evidence:" in out, True)
        expect("unconfigured knowledge / tasks bullets left out",
               "librarian` agent writes" in out or "scheduler` agent" in out, False)
        expect("configured reminders bullet kept", "Reminder hooks" in out, True)
        (USER_DIR / "habits.json").write_text(json.dumps({"habits": {"drop": ["advisor", "nope"],
                                                                     "extra": ["- Mine: tabs, not spaces."]}}))
        out = run("session-start", s, proj)[1]
        expect("user drop / extra applied", "Advisor:" not in out and "- Mine: tabs" in out, True)
        expect("unknown drop id reported", "unknown habit id(s) in habits.drop: nope" in out, True)
        (USER_DIR / "habits.json").write_text("{broken")
        out = run("session-start", s, proj)[1]
        expect("broken user habits.json ignored", "Advisor:" in out, True)
        (USER_DIR / "habits.json").unlink()
        bare = Path(tmp) / "bare"
        bare.mkdir()
        out = run("session-start", {**s, "cwd": str(bare)}, bare)[1]
        expect("no config: no reminders bullet", "Reminder hooks" not in out and "- Evidence:" in out, True)
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
        note = {**t, "prompt": "<task-notification>\n<task-id>b1</task-id>\n</task-notification>"}
        expect("background notification quiet", run("prompt", note, proj)[1].strip(), "")
        # the user's text after a notification block, in the same prompt: still warns
        typed = {**t, "prompt": "<system-reminder>\n" + note["prompt"] + "\n</system-reminder>\nReview and then push."}
        out = run("prompt", typed, proj)[1]
        expect("big context warns (typed after a notification)", "systemMessage" in out and "250K" in out, True)
        expect("same step quiet", run("prompt", t, proj)[1].strip(), "")
        ctx(320_000)
        expect("next step warns again", "320K" in run("prompt", t, proj)[1], True)
        big = {**t, "tool_name": "Bash", "tool_input": {"command": "cat big"}, "tool_response": {"stdout": "x" * 40_000}}
        expect("big output nudged", "additionalContext" in run("post-tool", big, proj)[1], True)
        expect("subagent big output quiet", run("post-tool", {**big, "agent_id": "a1"}, proj)[1].strip(), "")
        edit = {**t, "tool_name": "Edit", "tool_input": {"file_path": str(proj / "docs" / "CHANGELOG.md")},
                "tool_response": {"originalFile": "x" * 40_000}}
        expect("edit of a big file quiet", run("post-tool", edit, proj)[1].strip(), "")
        small = {**big, "tool_response": {"stdout": "ok"}}
        expect("small output quiet", run("post-tool", small, proj)[1].strip(), "")
        img = {**t, "tool_name": "Read", "tool_input": {"file_path": "C:/x/shot.png"}, "tool_response": {}}
        expect("image at big context nudged", "Image read" in run("post-tool", img, proj)[1], True)
        # shell write guard (built in): heredoc / multi-line echo into a source file blocked, program-feeding passes
        def sh(cmd, tool="Bash"):
            return run("pre-tool", {**s, "tool_name": tool, "tool_input": {"command": cmd}}, proj)[0]
        expect("heredoc write blocked", sh("cat > src/new.py <<'EOF'\nx = 1\nEOF"), 2)
        expect("heredoc append blocked", sh("cat <<EOF >> CLAUDE.md\nline\nEOF"), 2)
        expect("heredoc tee blocked", sh("cat <<EOF | tee docs/a.md\nline\nEOF"), 2)
        expect("multi-line echo blocked", sh('echo "a\nb" > src/x.txt'), 2)
        expect("printf with newlines blocked", sh("printf 'a\\nb\\n' > src/x.txt"), 2)
        expect("git commit -F - heredoc passes", sh("git commit -F - <<'EOF'\nsubject\n\nbody\nEOF"), 0)
        expect("python - heredoc passes", sh("python - <<'EOF'\nprint(1)\nEOF"), 0)
        # review of 7fce131: a `>` in the fed text / value word isn't a write target
        expect("commit body with -> passes", sh("git commit -F - <<'EOF'\nfx: FAIL -> PASS\nEOF"), 0)
        expect("python - with > passes", sh("python - <<'EOF'\nprint(1 > 0)\nEOF"), 0)
        expect("ps here-string commit with -> passes", sh("git commit -m @'\na -> b\n'@", "PowerShell"), 0)
        expect("ps Set-Content to temp with value passes", sh('Set-Content $env:TEMP/x.txt "hi"', "PowerShell"), 0)
        expect("heredoc write after a fed heredoc blocked",
               sh("git commit -F - <<'EOF'\na -> b\nEOF\ncat > src/x.py <<EOF\nx\nEOF"), 2)
        expect("heredoc to temp passes", sh("cat > /tmp/x.json <<EOF\n{}\nEOF"), 0)
        expect("heredoc to scratchpad passes", sh("cat > \"C:/Users/u/AppData/Local/Temp/claude/s/scratchpad/in.json\" "
                                                  "<<EOF\n{}\nEOF"), 0)
        expect("heredoc with 2>&1 only passes", sh("python - <<EOF 2>&1\nprint(1)\nEOF"), 0)
        expect("single-line echo append passes", sh("echo 'x' >> .gitignore"), 0)
        expect("program output redirect passes", sh("echo start\npython t.py > out.txt\nhead out.txt"), 0)
        expect("ps here-string Set-Content blocked", sh("@'\nline\n'@ | Set-Content -Path src/a.txt", "PowerShell"), 2)
        expect("ps Set-Content blocked", sh("Set-Content src/a.txt $x", "PowerShell"), 2)
        code, out = run("pre-tool", {**s, "tool_name": "PowerShell", "tool_input": {
            "command": "@'\nl1\nl2\n'@ | Set-Content -Encoding utf8 scripts/Main.lua"}}, proj)
        expect("ps positional target after flag named", code == 2 and "scripts/Main.lua" in out, True)
        expect("ps here-string commit passes", sh("git commit -m @'\nsubject\n'@", "PowerShell"), 0)
        expect("ps Out-File to temp passes", sh("@'\nx\n'@ | Out-File $env:TEMP/x.txt", "PowerShell"), 0)
        # inline Python / sed -i writes (the librarian's runs of 2026-10-31 edited the docs this way)
        code, out = run("pre-tool", {**s, "tool_name": "Bash", "tool_input": {"command":
            "cd /d/proj; python - <<'E'\nimport io\np='docs/knowledge/ENGINE_NOTES.md'\ns=io.open(p,encoding='utf-8').read()\n"
            "io.open(p,'w',encoding='utf-8',newline='\\n').write(s.replace('a','b'))\nE"}}, proj)
        expect("python heredoc rewriting a doc blocked", code == 2 and "docs/knowledge/ENGINE_NOTES.md" in out, True)
        expect("python -c open(..., 'a') blocked", sh("python -c \"open('src/x.py', 'a').write('y')\""), 2)
        expect("python Path.write_text blocked",
               sh("python3 - <<EOF\nfrom pathlib import Path\nPath('docs/a.md').write_text('x')\nEOF"), 2)
        expect("python computed target with a doc path blocked",
               sh("python - <<EOF\nd='docs'\nf=open(d+'/a.md','w')\nEOF"), 2)
        expect("sed -i on .md blocked", sh("cd docs && sed -i '466s/p-anim-7/p-anim-8/' PITFALLS.md"), 2)
        expect("python reading a doc passes", sh("python - <<'E'\nprint(open('docs/a.md').read()[:50])\nE"), 0)
        expect("python open(..., 'rb') passes", sh("python -c \"open('a.bin', 'rb').read()\""), 0)
        expect("python writing the scratchpad passes",
               sh("python - <<E\nopen('C:/Users/u/AppData/Local/Temp/claude/s/scratchpad/o.txt','w').write('x')\nE"), 0)
        expect("python writing a temp var passes",
               sh("python - <<E\nout = '/tmp/r.csv'\nopen(out, 'w').write('x')\nE"), 0)
        expect("python writing $S (scratchpad var) passes",
               sh("S=C:/Users/u/AppData/Local/Temp/claude/s/scratchpad; python - <<E\nopen('$S/x.py','w')\nE"), 0)
        expect("python after cd into the scratchpad passes",
               sh("mkdir -p \"C:/Users/u/AppData/Local/Temp/claude/s/scratchpad/a\" && cd \"$_\" && python - <<E\n"
                  "open('plan.py','w').write('x')\nE"), 0)
        subprocess.run(["git", "init", "-q", str(proj)], check=True)
        (proj / ".gitignore").write_text("out/\n")
        expect("python writing a git-ignored out/ file passes",
               sh("python - <<E\nopen('out/table.csv','w').write('x')\nE"), 0)
        expect("python writing a tracked-area file in a repo blocked",
               sh("python - <<E\nopen('src/new.py','w').write('x')\nE"), 2)
        expect("python script from a file passes", sh("python -I tools/export.py --out out/run1"), 0)
        expect("sed -i on .lua passes (not markdown)", sh("sed -i 's/a/b/' src/x.lua"), 0)
        expect("sed without -i on .md passes", sh("sed -n 1,20p docs/a.md"), 0)
        expect("commit body naming open(..., 'w') passes",
               sh("git commit -F - <<'EOF'\nfix: open(p, 'w') truncated docs/a.md\nEOF"), 0)
        # control-character guard (built in): NUL / form feed / invalid UTF-8 / mixed endings reported after an edit
        def edited(name, content):
            f = proj / "src" / name
            f.write_bytes(content)
            return run("post-tool", {**s, "tool_name": "Edit", "tool_input": {"file_path": str(f)}}, proj)
        code, out = edited("nul.md", b"ok\n\x00bad\n")
        expect("NUL byte reported", code == 2 and "NUL at line 2" in out, True)
        code, out = edited("ff.md", b"ok\n\x0cbad\n")
        expect("form feed reported", code == 2 and "form feed" in out, True)
        code, out = edited("cp1252.md", "caf\xe9\n".encode("cp1252"))
        expect("invalid UTF-8 reported", code == 2 and "UTF-8" in out, True)
        code, out = edited("mixed.md", b"a\r\nb\nc\r\n")
        expect("mixed line endings reported", code == 2 and "mixed line endings" in out, True)
        expect("clean CRLF file passes", edited("crlf.md", "caf\xe9\r\n\tok\r\n".encode("utf-8"))[0], 0)
        expect("clean LF file passes", edited("lf.md", b"a\nb\n")[0], 0)
        expect("unknown binary format reported", edited("x.blob", b"\x00\x01")[0], 2)
        cfg = json.loads((proj / ".claude" / "habits.json").read_text())
        (proj / ".claude" / "habits.json").write_text(json.dumps({**cfg, "guards": {"binary_ext": ["blob"]}}))
        expect("guards.binary_ext format skipped", edited("y.BLOB", b"\x00\x01")[0], 0)
        (proj / ".claude" / "habits.json").write_text(json.dumps(cfg))
        # experiments reminder: first long-run launch without a plan -> once; with a plan edit -> none
        cfg = json.loads((proj / ".claude" / "habits.json").read_text())
        cfg["experiments"] = {"on_command": [r"run_suite|tools[/\\]bench[/\\]trace\.py"],
                              "plan": [r"/test-logs/.*\.md$"]}
        (proj / ".claude" / "habits.json").write_text(json.dumps(cfg))
        launch = {**s, "tool_name": "Bash", "tool_input": {"command": "python -I tools/bench/trace.py specs/a.json"}}
        code, out = run("pre-tool", launch, proj)
        expect("first launch without plan reminds", code == 0 and "experiment plan" in out and "PreToolUse" in out, True)
        expect("second launch quiet", run("pre-tool", launch, proj)[1].strip(), "")
        s2 = {**s, "session_id": s["session_id"] + "b"}
        run("post-tool", {**s2, "tool_name": "Write", "tool_input": {"file_path": str(proj / "docs/test-logs/p.md")}},
            proj)
        expect("launch after plan quiet", run("pre-tool", {**s2, **launch}, proj)[1].strip(), "")
        expect("unrelated command quiet", run("pre-tool", {**s2, "tool_name": "Bash",
                                                           "tool_input": {"command": "git status"}}, proj)[1].strip(), "")
        # knowledge guard: only the librarian agent writes knowledge_paths (Edit tools and shell writes)
        cfg = json.loads((proj / ".claude" / "habits.json").read_text())
        cfg["knowledge_paths"] = [r"/docs/knowledge/", r"/memory/[^/]+\.md$"]
        cfg["reminders"].append({"name": "kb", "on_search": ["engine"], "docs": ["docs/knowledge"],
                                 "to_librarian": True, "message": "record what was learned"})
        (proj / ".claude" / "habits.json").write_text(json.dumps(cfg))
        kb = str(proj / "docs" / "knowledge" / "ENGINE.md")

        def tool(name, inp, **extra):
            return run("pre-tool", {**s, "tool_name": name, "tool_input": inp, **extra}, proj)
        code, out = tool("Edit", {"file_path": kb})
        expect("main-session knowledge edit blocked", code == 2 and "agent-habits:librarian" in out, True)
        expect("librarian knowledge edit passes", tool("Edit", {"file_path": kb}, agent_id="a2",
                                                       agent_type="agent-habits:librarian")[0], 0)
        expect("other subagent knowledge write blocked", tool("Write", {"file_path": kb}, agent_id="a3",
                                                              agent_type="Explore")[0], 2)
        expect("memory write blocked", tool("Write", {"file_path": "C:/u/.claude/projects/p/memory/x.md"})[0], 2)
        expect("edit outside knowledge passes", tool("Edit", {"file_path": str(proj / "docs" / "CHANGELOG.md")})[0], 0)
        expect("shell append to knowledge blocked", sh("echo x >> docs/knowledge/ENGINE.md"), 2)
        expect("sed -i on knowledge blocked", sh("sed -i 's/a/b/' docs/knowledge/ENGINE.md"), 2)
        expect("cp into knowledge blocked", sh("cp notes.md docs/knowledge/"), 2)
        expect("ps Move-Item from knowledge blocked", sh("Move-Item docs/knowledge/ENGINE.md old.md", "PowerShell"), 2)
        expect("env-prefixed sed -i on knowledge blocked", sh("LC_ALL=C sed -i 's/a/b/' docs/knowledge/ENGINE.md"), 2)
        expect("git rm of knowledge blocked", sh("git rm -q docs/knowledge/ENGINE.md"), 2)
        expect("ps Set-Content into knowledge blocked",
               sh("Set-Content -Encoding utf8 docs/knowledge/ENGINE.md $x", "PowerShell"), 2)
        expect("ps Set-Content of a knowledge path string into temp passes",
               sh("Set-Content $env:TEMP/log.txt 'docs/knowledge/ENGINE.md'", "PowerShell"), 0)
        expect("git checkout of other paths passes", sh("git checkout -- src/ok.py"), 0)
        expect("cp out of knowledge passes", sh("cp docs/knowledge/ENGINE.md /tmp/x.md"), 0)
        expect("reading knowledge passes", sh("grep -n fact docs/knowledge/ENGINE.md > /tmp/hits.txt"), 0)
        expect("git add knowledge passes", sh("git add docs/knowledge/ENGINE.md && git commit -m 'kb: x'"), 0)
        expect("knowledge path in commit body passes",
               sh("git commit -F - <<'EOF'\nkb: merge\n\nmoved > docs/knowledge/ENGINE.md\nEOF"), 0)
        # issue #2: a `<<<` here-string is no heredoc, so the lines after it are still checked
        expect("knowledge write after a here-string blocked",
               sh('cat <<< "$x"\necho hi > docs/knowledge/ENGINE.md'), 2)
        expect("sed -i on knowledge after a here-string blocked",
               sh("grep a <<< \"$x\"\nsed -i 's/a/b/' docs/knowledge/ENGINE.md"), 2)
        expect("source write after a here-string blocked", sh('cat <<< "$x"\ncat > src/x.py <<EOF\nx\nEOF'), 2)
        lib = {**s, "agent_type": "agent-habits:librarian"}
        code, out = run("pre-tool", {**lib, "tool_name": "Bash",
                                     "tool_input": {"command": "sed -i 's/a/b/' docs/knowledge/ENGINE.md"}}, proj)
        expect("librarian sed -i on a doc blocked (Edit tool only)", code == 2 and "Inline write" in out, True)
        code, out = run("pre-tool", {**lib, "tool_name": "Bash",
                                     "tool_input": {"command": "git mv docs/knowledge/A.md docs/knowledge/B.md"}}, proj)
        expect("librarian git mv in knowledge passes", code, 0)
        code, out = run("pre-tool", {**lib, "tool_name": "Edit",
                                     "tool_input": {"file_path": str(proj / "docs" / "knowledge" / "ENGINE.md")}}, proj)
        expect("librarian Edit of knowledge passes", code, 0)
        # tracker guard: only the scheduler agent writes trackers.paths (index, plan); task files stay open
        cfg = json.loads((proj / ".claude" / "habits.json").read_text())
        cfg["trackers"] = {"map": "docs/tasks/README.md",
                           "paths": [r"/docs/tasks/README\.md$", r"/docs/tasks/PLAN\.md$"]}
        (proj / ".claude" / "habits.json").write_text(json.dumps(cfg))
        plan = str(proj / "docs" / "tasks" / "PLAN.md")
        code, out = tool("Edit", {"file_path": plan})
        expect("main-session plan edit blocked", code == 2 and "agent-habits:scheduler" in out, True)
        expect("scheduler plan edit passes", tool("Edit", {"file_path": plan}, agent_id="a4",
                                                  agent_type="agent-habits:scheduler")[0], 0)
        expect("librarian plan edit blocked", tool("Edit", {"file_path": plan}, agent_id="a5",
                                                   agent_type="agent-habits:librarian")[0], 2)
        expect("scheduler knowledge edit blocked", tool("Edit", {"file_path": kb}, agent_id="a6",
                                                        agent_type="agent-habits:scheduler")[0], 2)
        expect("task progress edit passes",
               tool("Edit", {"file_path": str(proj / "docs" / "tasks" / "T-3-x.md")})[0], 0)
        expect("shell append to the index blocked", sh("echo '| T-4 |' >> docs/tasks/README.md"), 2)
        # trackers.map / paths derived from "tasks" alone (index, plan, done list; task files stay open)
        cfg.pop("trackers")
        cfg["tasks"] = {"dir": "docs/tasks", "prefix": "T-"}
        (proj / ".claude" / "habits.json").write_text(json.dumps(cfg))
        expect("tasks-only config: index, plan, done list blocked",
               [tool("Edit", {"file_path": str(proj / "docs" / "tasks" / f)})[0]
                for f in ("README.md", "PLAN.md", "done/README.md")], [2, 2, 2])
        expect("tasks-only config: task file and done task pass",
               [tool("Edit", {"file_path": str(proj / "docs" / "tasks" / f)})[0]
                for f in ("T-3-x.md", "done/T-2-y.md")], [0, 0])
        run("post-tool", {**s, "tool_name": "WebSearch", "tool_input": {"query": "engine timing"}}, proj)
        code, out = run("stop", s, proj)
        expect("stop reminder points at the librarian", code == 2 and "librarian" in out, True)
        # kb_audit: mechanical checks on a small knowledge base in a git repo
        kbp = proj / "kbproj"
        (kbp / "docs" / "knowledge").mkdir(parents=True)
        (kbp / "src").mkdir()
        (kbp / "src" / "engine_moved.py").write_text("def wave_land():\n    pass\n")
        (kbp / "src" / "café.py").write_text("x = 1\n")
        (kbp / ".claude").mkdir()
        (kbp / ".claude" / "habits.json").write_text(json.dumps({"knowledge_audit": {
            "files": ["docs/knowledge"], "code_roots": ["."], "report": "docs/knowledge/AUDIT.md"}}))
        (kbp / "docs" / "knowledge" / "A.md").write_text(
            "# Engine\n<a id=\"e-fall\"></a>\n### Fall speed is capped\nSee [B](B.md#nowhere) and [gone](C.md).\n"
            "- **Evidence:** `src/old/engine_moved.py`, `src/never_was.py`, commit `abc1234`, `lib/café.py`\n"
            "- **Used in:** `wave_land`, `noSuchFunction`, `Engine:wave_land`\n"
            "  - nested:\n    - see [nested](NESTED.md)\n\n    [in an indented code block](CODE.md)\n",
            encoding="utf-8")
        (kbp / "docs" / "knowledge" / "B.md").write_text("# B\n<a id=\"e-fall\"></a>\n### Fall speed is capped\n"
                                                         "[spaced](run%2092.md)\n")
        (kbp / "docs" / "knowledge" / "run 92.md").write_text("x\n")
        (kbp / "docs" / "knowledge" / "AUDIT.md").write_text("[x](missing.md)\n")
        subprocess.run(["git", "init", "-q", str(kbp)], check=True)
        subprocess.run(["git", "-C", str(kbp), "add", "-A"], check=True)
        r = subprocess.run([sys.executable, "-I", str(HABITS.parent / "kb_audit.py"), "--project", str(kbp), "--json"],
                           capture_output=True, text=True)
        try:
            got = {k: sorted(d["detail"] for d in v) for k, v in json.loads(r.stdout).items()}
        except ValueError:
            got = {"error": r.stdout + r.stderr}
        want = {"broken link": ["C.md", "NESTED.md"], "missing anchor": ["B.md#nowhere"],
                "moved path?": ["lib/café.py -> now kbproj/src/café.py",
                                "src/old/engine_moved.py -> now kbproj/src/engine_moved.py"],
                "missing path": ["src/never_was.py"], "unknown commit": ["abc1234"],
                "Used in name not in code": ["noSuchFunction"],
                "duplicate title": ["'fall speed is capped' in A.md, B.md"], "duplicate anchor": ["e-fall x2"]}
        expect("kb_audit finds each kind once, skips the report", got == want, True)
        if got != want:
            print("   got", got)
        # issue #2: habits.json found under cwd (a subproject), not CLAUDE_PROJECT_DIR -> its docs paths resolve there
        bare = proj / "bare"   # CLAUDE_PROJECT_DIR without a habits.json of its own
        sub = bare / "subproj"
        (sub / ".claude").mkdir(parents=True)
        (sub / "src").mkdir()
        (sub / "docs").mkdir()
        (sub / ".claude" / "habits.json").write_text(json.dumps({"reminders": [
            {"name": "changelog", "on_edit": [r"/src/"], "docs": ["docs/CHANGELOG.md"], "message": "add a line"}]}))
        ss = {"session_id": s["session_id"] + "sub", "cwd": str(sub)}
        run("post-tool", {**ss, "tool_name": "Edit", "tool_input": {"file_path": str(sub / "src" / "a.txt")}}, bare)
        time.sleep(0.05)
        (sub / "docs" / "CHANGELOG.md").write_text("line")
        expect("subproject docs edit counts", run("stop", ss, bare)[0], 0)
        # broken config never blocks
        (proj / ".claude" / "habits.json").write_text("{ not json")
        expect("broken config exits 0", run("pre-tool", {**s, "tool_name": "Bash",
                                                        "tool_input": {"command": "deploy prod"}}, proj)[0], 0)

    # kb_audit: rename guesses, entry parsing (the index's rows)
    sys.path.insert(0, str(HABITS.parent))
    import kb_audit
    toks = {"SPEED", "SI_TIMER0", "WALK.animation", "WALK.ANIMS", "LEDGE_JUMP", "LEDGE_Y", "walk", "start"}
    expect("rename: prefix dropped", kb_audit.rename_candidates("AD_SPEED", toks), ["SPEED"])
    expect("rename: middle part dropped", kb_audit.rename_candidates("SI_AD_TIMER0", toks), ["SI_TIMER0"])
    expect("rename: camelCase -> table field", kb_audit.rename_candidates("walkAnimation", toks), ["WALK.animation"])
    expect("rename: constant -> table field", kb_audit.rename_candidates("WALK_ANIMS", toks), ["WALK.ANIMS"])
    expect("rename: two candidates listed", kb_audit.rename_candidates("LEDGE_JUMP_Y", toks), ["LEDGE_JUMP", "LEDGE_Y"])
    expect("rename: no one-word camel guess", kb_audit.rename_candidates("startRoll", toks), [])
    doc = ('<a id="r2-x"></a>\n### Fact one\nText.\n- **Status:** Tested\n- **Used in:** `waveLand`, `SPEED`\n\n'
           '## Plain heading\n- **Status:** Guess (needs a run)\n')
    expect("index rows", kb_audit.entries(Path("ENGINE.md"), doc),
           [(1, "r2-x", "Fact one", "Tested", ["waveLand", "SPEED"]), (7, "plain-heading", "Plain heading", "Guess", [])])

    # agent_stats: a background run whose file the main session re-reads after the notification, a foreground run
    # whose file is read only before its answer and past the window
    import agent_stats
    with tempfile.TemporaryDirectory() as td:
        sub = Path(td) / "s1" / "subagents"
        sub.mkdir(parents=True)

        def jl(path, rows):
            path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

        def asst(*content, usage=None, side=False):
            return {"type": "assistant", "isSidechain": side, "timestamp": "2026-11-01T10:00:00Z",
                    "message": {"model": "claude-x", "content": list(content), "usage": usage or {}}}

        def read(p):
            return {"type": "tool_use", "name": "Read", "input": {"file_path": p}}
        for name, tid, shape, path, answer in (
                ("a1", "toolu_A", "background", "D:\\r\\x.py", {"type": "tool_use", "name": "SubagentHandback",
                                                                 "input": {"message": "found it"}}),
                ("a2", "toolu_B", "foreground", "D:/r/y.py", {"type": "text", "text": "done"})):
            (sub / f"{name}.meta.json").write_text(json.dumps({"agentType": "habits:finder", "toolUseId": tid,
                                                               "requestShape": shape}), encoding="utf-8")
            jl(sub / f"{name}.jsonl", [asst(read(path), usage={"input_tokens": 10, "cache_read_input_tokens": 100,
                                                               "output_tokens": 5}, side=True),
                                       asst(answer, side=True)])
        launch = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_A",
                                                           "content": "Async agent launched successfully."}]}}
        jl(Path(td) / "s1.jsonl", [
            launch,
            asst(read("d:/r/y.py")),                       # before B's answer: not a re-read
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_B",
                                                      "content": "done"}]}},
            asst({"type": "tool_use", "name": "Bash", "input": {}}),
            asst({"type": "tool_use", "name": "Bash", "input": {}}),
            {"type": "attachment", "attachment": {"prompt": "<task-notification><tool-use-id>toolu_A</tool-use-id>"}},
            asst(read("D:/r/x.py")),                       # 1 call after A's answer: re-read
            asst(read("D:/r/y.py")),                       # 3 calls after B's answer: past window 2
        ])
        runs = {r["tool_use_id"]: r for r in agent_stats.collect([Path(td)], window=2)}
        expect("agent_stats: two runs", sorted(runs), ["toolu_A", "toolu_B"])
        expect("agent_stats: hand-back is the answer, not a tool", (runs["toolu_A"]["answer"], runs["toolu_A"]["tools"]),
               ("found it", 1))
        expect("agent_stats: tokens fresh / cache read", (runs["toolu_A"]["fresh"], runs["toolu_A"]["cache_read"]),
               (15, 100))
        expect("agent_stats: re-read after notification", runs["toolu_A"]["rereads"], ["d:/r/x.py"])
        expect("agent_stats: no re-read before answer or past window", runs["toolu_B"]["rereads"], [])
        row = agent_stats.summarize(list(runs.values()))[0]
        expect("agent_stats: summary row", (row["runs"], row["background"], row["reread_runs"], row["runs_with_reads"]),
               (2, 1, 1, 2))

    # tasks.py: a project from the templates with two open tasks (one blocked), a lane, one closed task
    plugin = HABITS.parent.parent
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        tdir = root / "work" / "tasks"
        (tdir / "done").mkdir(parents=True)
        (root / ".claude").mkdir()
        (root / ".claude" / "habits.json").write_text(json.dumps({"tasks": {"dir": "work/tasks", "prefix": "W-"}}))
        for rel in ("README.md", "PLAN.md", "done/README.md"):
            text = (plugin / "templates" / "tasks" / rel).read_text(encoding="utf-8")
            (tdir / rel).write_text(text.replace("T-", "W-").replace("| A: main | |", "| A: main | W-1, W-2, W-3 |"),
                                    encoding="utf-8")
        (tdir / "W-1-login.md").write_text(
            "# W-1: Login page\n\n**Status:** open\n**Links:** [PROJ-7](https://jira.example/PROJ-7)\n\n"
            "## State when written (2026-11-01)\n", encoding="utf-8")
        (tdir / "W-2-search.md").write_text("# W-2: Search\n\n**Status:** Blocked(API key)\n"
                                            "**Needs you:** which search API?\nSeen 2026-10-01.\n", encoding="utf-8")
        (tdir / "done" / "W-3-setup.md").write_text("# W-3: Setup\n\n**Status:** open\n\n## Resolution (2026-10-30)\n"
                                                    "Done: repo and CI set up.\n", encoding="utf-8")
        (tdir / "done" / "W-4-old.md").write_text("# W-4: Old\n\n## Resolution\nDropped.\n", encoding="utf-8")
        (tdir / "done" / "W-5-new.md").write_text("# W-5: New\n\n## Resolution (2026-10-31)\nDone.\n",
                                                  encoding="utf-8")

        def tasks(*args):
            r = subprocess.run([sys.executable, "-I", str(HABITS.parent / "tasks.py"), "--project", str(root), *args],
                               capture_output=True, text=True)
            return r.returncode, r.stdout
        code, out = tasks()
        expect("tasks.py: lane naming a closed task reported", code == 1 and "W-3 is closed" in out, True)
        plan = (tdir / "PLAN.md").read_text(encoding="utf-8")
        (tdir / "PLAN.md").write_text(plan.replace("W-1, W-2, W-3", "W-1, W-2"), encoding="utf-8")
        expect("tasks.py: rewrites, then --check passes", (tasks()[0], tasks("--check")[0]), (0, 0))
        index = (tdir / "README.md").read_text(encoding="utf-8")
        plan = (tdir / "PLAN.md").read_text(encoding="utf-8")
        done = (tdir / "done" / "README.md").read_text(encoding="utf-8")
        expect("tasks.py: index row with links and date",
               "| [W-1](W-1-login.md) | Login page · [PROJ-7](https://jira.example/PROJ-7) | open | 2026-11-01 | — |"
               in index, True)
        expect("tasks.py: plan board", ("- [W-2](W-2-search.md) which search API?" in plan,
                                        "- A: main: W-1 open -> W-2 blocked" in plan), (True, True))
        expect("tasks.py: Written only from the State when written heading",
               "| Blocked(API key) | — | which search API? |" in index, True)
        expect("tasks.py: done list", "| [W-3](W-3-setup.md) | Setup | 2026-10-30 | Done: repo and CI set up. |" in done,
               True)
        expect("tasks.py: done list newest first, undated last",
               [done.find(f"[W-{n}]") for n in (5, 3, 4)] == sorted(done.find(f"[W-{n}]") for n in (5, 3, 4)), True)
        (tdir / "W-1-login.md").write_text("# W-1: Login page\n\n**State:** open\n", encoding="utf-8")
        code, out = tasks("--check")
        expect("tasks.py: broken header fails --check", code == 1 and "W-1-login.md: no '**Status:**" in out, True)
        (tdir / "W-1-login.md").write_text("# W-1: Login page\n\n**Status:** done\n", encoding="utf-8")
        code, out = tasks("--check")
        expect("tasks.py: bad Status value named", code == 1 and "Status 'done' is not open" in out, True)
        r = subprocess.run([sys.executable, "-I", str(HABITS.parent / "tasks.py"), "--project"], capture_output=True)
        expect("tasks.py: --project without a value", r.returncode, 2)
        # Priority header + the plan's Order queue
        (tdir / "W-1-login.md").write_text("# W-1: Login page\n\n**Status:** open\n**Priority:** P2\n", encoding="utf-8")
        (tdir / "W-7-api.md").write_text("# W-7: API\n\n**Status:** open\n", encoding="utf-8")
        code, out = tasks("--check")
        expect("tasks.py: Priority on some tasks needs it on all", code == 1 and "W-7-api.md: no '**Priority:**" in out,
               True)
        (tdir / "W-7-api.md").write_text("# W-7: API\n\n**Status:** open\n**Priority:** P1 (soon)\n", encoding="utf-8")
        (tdir / "W-2-search.md").write_text("# W-2: Search\n\n**Status:** open\n**Priority:** P2\n", encoding="utf-8")
        plan = "# Plan\n\nOrder: W-2, W-1, W-3\n\n<!-- board: x -->\n<!-- /board -->\n"
        (tdir / "PLAN.md").write_text(plan, encoding="utf-8")
        code, out = tasks()
        expect("tasks.py: Order naming a closed task reported", code == 1 and "Order: W-3 is closed" in out, True)
        (tdir / "PLAN.md").write_text(plan.replace(", W-3", ""), encoding="utf-8")
        code, out = tasks()
        plan = (tdir / "PLAN.md").read_text(encoding="utf-8")
        expect("tasks.py: queue by Priority, then Order",
               (code, [plan.find(f"[W-{n}]") for n in (7, 2, 1)] == sorted(plan.find(f"[W-{n}]") for n in (7, 2, 1))),
               (0, True))
        expect("tasks.py: Priority column in the index", "| [W-7](W-7-api.md) | API | open | P1 |"
               in (tdir / "README.md").read_text(encoding="utf-8"), True)
        code, out = tasks("--next")
        expect("tasks.py: --next lists the queue head", (code, "1. P1 W-7 API [open]" in out), (0, True))
        (tdir / "W-7-api.md").unlink()
        (tdir / "W-1-login.md").write_text("# W-1: Login page\n\n**Status:** open\n", encoding="utf-8")
        (tdir / "W-2-search.md").write_text("# W-2: Search\n\n**Status:** open\n", encoding="utf-8")
        (tdir / "PLAN.md").unlink()
        (tdir / "W-1-login.md").write_text("# W-1: Login page\n\n**Status:** started\n", encoding="utf-8")
        expect("tasks.py: no plan file is fine", (tasks()[0], tasks("--check")[0]), (0, 0))
        # list file name from config: index (and the done list) named TASKS.md
        (root / ".claude" / "habits.json").write_text(json.dumps({"tasks": {"dir": "work/tasks", "prefix": "W-",
                                                                            "index": "TASKS.md"}}))
        (tdir / "README.md").rename(tdir / "TASKS.md")
        (tdir / "done" / "README.md").rename(tdir / "done" / "TASKS.md")
        (tdir / "done" / "W-6-x.md").write_text("# W-6: Six\n\n## Resolution (2026-11-01)\nDone.\n", encoding="utf-8")
        tasks()
        expect("tasks.py: done list file from config", "[W-6]" in (tdir / "done" / "TASKS.md").read_text("utf-8"),
               True)

    # telemetry: the receiver only starts when telemetry goes to localhost; bad bodies get a 400 and a marker line
    tel = HABITS.parent / "telemetry"
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); import ensure_receiver as e; print([e.local_port(env) for "
             "env in ({}, {'CLAUDE_CODE_ENABLE_TELEMETRY': '1', 'OTEL_EXPORTER_OTLP_ENDPOINT': 'http://127.0.0.1:4999'},"
             " {'CLAUDE_CODE_ENABLE_TELEMETRY': '1', 'OTEL_EXPORTER_OTLP_ENDPOINT': 'https://otel.example.com'})])")
    r = subprocess.run([sys.executable, "-I", "-c", probe, str(tel)], capture_output=True, text=True)
    expect("telemetry: receiver only for a local endpoint", r.stdout.strip(), "[None, 4999, None]")
    import socket
    import urllib.error
    import urllib.request
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with tempfile.TemporaryDirectory() as td:
        srv = subprocess.Popen([sys.executable, "-I", str(tel / "receiver.py"), "--port", str(port), "--out", td])
        try:
            def post(body):
                req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/logs", data=body,
                                             headers={"Content-Type": "application/json"})
                try:
                    return urllib.request.urlopen(req, timeout=2).status
                except urllib.error.HTTPError as e:
                    return e.code
            for _ in range(50):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2)
                    break
                except OSError:
                    time.sleep(0.1)
            codes = [post(b'{"resourceLogs": []}'), post(b"[1]"), post(b"{bad")]
            lines = [ln for f in Path(td).glob("*/logs.jsonl") for ln in f.read_text("utf-8").splitlines()]
            expect("telemetry: receiver answers object / array / broken", (codes, len(lines),
                   sum('"_bad_body"' in ln for ln in lines)), ([200, 400, 400], 3, 2))
        finally:
            srv.kill()
            srv.wait()

    # check_test_logs.py: every tracked file in the configured log folders named in a tracked .md
    if shutil.which("git"):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "logs").mkdir()
            (root / ".claude").mkdir()
            for name, text in (("logs/run1.txt", "x"), ("logs/run10.txt", "x"), ("TESTS.md", "run10 passed\n")):
                (root / name).write_text(text, encoding="utf-8")

            def check_logs():
                subprocess.run(["git", "-C", td, "add", "-A"], capture_output=True)
                r = subprocess.run([sys.executable, "-I", str(HABITS.parent / "check_test_logs.py"), "--project", td],
                                   capture_output=True, text=True)
                return r.returncode, r.stdout.split()
            subprocess.run(["git", "init", "-q", td], capture_output=True)
            expect("check_test_logs: no config passes", check_logs()[0], 0)
            (root / ".claude" / "habits.json").write_text(json.dumps({"test_logs": {"dirs": ["logs"]}}))
            expect("check_test_logs: run1 unnamed (run10 doesn't count)", check_logs(), (1, ["logs/run1.txt"]))
            (root / "TESTS.md").write_text("run10 passed\nrun1.txt failed\n", encoding="utf-8")
            expect("check_test_logs: all named", check_logs(), (0, []))

    # git hooks from templates/githooks in a temp repo (needs git and sh)
    if shutil.which("git"):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)

            def git(*args, ok=True):
                r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
                if ok and r.returncode:
                    raise RuntimeError(f"git {args}: {r.stdout}{r.stderr}")
                return r.returncode, r.stdout + r.stderr

            def hab(c):
                (repo / ".claude" / "habits.json").write_text(json.dumps(c))

            git("init", "-q", "-b", "main")
            git("config", "user.name", "t")
            git("config", "user.email", "t@t")
            (repo / ".claude").mkdir()
            (repo / ".githooks").mkdir()
            for h in ("pre-commit", "commit-msg"):
                shutil.copy(plugin / "templates" / "githooks" / h, repo / ".githooks" / h)
                os.chmod(repo / ".githooks" / h, 0o755)
            (repo / "a.txt").write_text("0\n")
            hab({})
            git("add", ".")
            git("commit", "-qm", "t: start")
            git("config", "core.hooksPath", ".githooks")
            (repo / "a.txt").write_text("1\n")
            git("add", "a.txt")
            expect("hook: guard off by default, whole-index commit passes", git("commit", "-qm", "t: one", ok=False)[0],
                   0)
            hab({"shared_index_guard": True, "precommit": ["cat > /dev/null", "echo WARN second-ran"]})
            (repo / "a.txt").write_text("2\n")
            git("add", "a.txt", ".claude/habits.json")
            code, out = git("commit", "-m", "t: two", ok=False)
            expect("hook: guard on blocks a whole-index commit", code == 1 and "whole shared index" in out, True)
            code, out = git("commit", "-m", "t: two", "--", "a.txt", ".claude/habits.json", ok=False)
            expect("hook: pathspec commit passes, stdin-reading check doesn't swallow the next",
                   (code, "WARN second-ran" in out), (0, True))
            hab({"shared_index_guard": True, "precommit": "exit 3"})
            (repo / "b.txt").write_text("b\n")
            code, out = git("commit", "-m", "t: three", "--", ".claude/habits.json", ok=False)
            expect("hook: precommit as a string, failure blocks", code == 1 and "exit 3 failed" in out, True)
            hab({"shared_index_guard": True})
            git("commit", "-qm", "t: three", "--", ".claude/habits.json")
            (repo / ".claude" / "habits.json").write_text("{bad")
            code, out = git("commit", "-m", "t: four", "--", ".claude/habits.json", ok=False)
            expect("hook: invalid habits.json named", code == 1 and "not valid JSON" in out, True)
            hab({"shared_index_guard": True})
            git("checkout", "-qb", "side")
            (repo / "a.txt").write_text("side\n")
            git("commit", "-qm", "t: side", "--", "a.txt")
            git("checkout", "-q", "main")
            (repo / "a.txt").write_text("main\n")
            git("commit", "-qm", "t: main", "--", "a.txt")
            git("cherry-pick", "side", ok=False)
            (repo / "a.txt").write_text("both\n")
            git("add", "a.txt")
            expect("hook: cherry-pick conflict commit passes the guard", git("commit", "--no-edit", ok=False)[0], 0)
            code, out = git("commit", "--allow-empty", "-m", "Bad subject", ok=False)
            expect("hook: commit-msg blocks a bad subject", code == 1 and "lowercase area" in out, True)

    # a plain YAML value with ": " fails to parse and Claude Code drops the whole frontmatter (model, tools)
    plugin = HABITS.parent.parent
    for f in [*plugin.glob("agents/*.md"), *plugin.glob("skills/*/SKILL.md")]:
        front = f.read_text(encoding="utf-8").split("---")[1]
        bad = [k for k, _, v in (l.partition(": ") for l in front.splitlines() if ": " in l)
               if ": " in v and not v.lstrip().startswith(("'", '"', "|", ">"))]
        expect(f"frontmatter parses: {f.relative_to(plugin).as_posix()} {bad or ''}", bool(bad), False)
    print("all passed" if not fails else f"{len(fails)} failed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
