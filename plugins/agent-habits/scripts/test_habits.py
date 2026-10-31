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
            "command": "@'\nl1\nl2\n'@ | Set-Content -Encoding utf8 Lua/Captain.Lua"}}, proj)
        expect("ps positional target after flag named", code == 2 and "Lua/Captain.Lua" in out, True)
        expect("ps here-string commit passes", sh("git commit -m @'\nsubject\n'@", "PowerShell"), 0)
        expect("ps Out-File to temp passes", sh("@'\nx\n'@ | Out-File $env:TEMP/x.txt", "PowerShell"), 0)
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
        # experiments reminder: first long-run launch without a plan -> once; with a plan edit -> none
        cfg = json.loads((proj / ".claude" / "habits.json").read_text())
        cfg["experiments"] = {"on_command": [r"run_pie_session|tools[/\\]dolphin[/\\]trace\.py"],
                              "plan": [r"/test-logs/.*\.md$"]}
        (proj / ".claude" / "habits.json").write_text(json.dumps(cfg))
        launch = {**s, "tool_name": "Bash", "tool_input": {"command": "python -I tools/dolphin/trace.py specs/a.json"}}
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
        code, out = run("pre-tool", {**s, "tool_name": "Bash", "agent_type": "agent-habits:librarian",
                                     "tool_input": {"command": "sed -i 's/a/b/' docs/knowledge/ENGINE.md"}}, proj)
        expect("librarian shell edit passes", code, 0)
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
            "- **Used in:** `wave_land`, `noSuchFunction`\n"
            "  - nested:\n    - see [nested](NESTED.md)\n\n    [in an indented code block](CODE.md)\n",
            encoding="utf-8")
        (kbp / "docs" / "knowledge" / "B.md").write_text("# B\n<a id=\"e-fall\"></a>\n### Fall speed is capped\n")
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
        # broken config never blocks
        (proj / ".claude" / "habits.json").write_text("{ not json")
        expect("broken config exits 0", run("pre-tool", {**s, "tool_name": "Bash",
                                                        "tool_input": {"command": "deploy prod"}}, proj)[0], 0)

    print("all passed" if not fails else f"{len(fails)} failed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
