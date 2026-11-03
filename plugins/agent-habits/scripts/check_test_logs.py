# copied from agent-habits <version> (plugins/agent-habits/scripts/check_test_logs.py); refresh with the habits-update skill
"""Every file in a test-log folder must be named in a doc, so old results are looked up in the index and write-ups,
not by opening raw logs.

    python -I tools/check_test_logs.py [--project <dir>]     (prints the unnamed files)

Config: .claude/habits.json "test_logs": {"dirs": ["<log folder>", ...], "doc_repos": ["../other-repo", ...]}
(paths relative to the project). Named = the file name, or its stem without the last extension, appears in a tracked
.md file outside the log folders (this repo and the doc_repos), not followed by a letter, digit, '-' or '_' (so
run1 doesn't count as run10). Exit 1 and list the unnamed files otherwise; no "test_logs" config = nothing to check.
Meant for the pre-commit (habits.json "precommit").
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def tracked(repo, *spec):
    # in a hook git sets GIT_INDEX_FILE (a temp index for `git commit -- <paths>`) / GIT_DIR: they would point
    # `git -C <other repo>` at this repo's index, so the other repo's docs looked untracked
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE")}
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--", *spec], capture_output=True, text=True,
                         encoding="utf-8", env=env)
    return [p for p in out.stdout.split("\0") if p]


def unnamed(repo, logs, doc_repos):
    """Tracked files under the `logs` folders of `repo` whose name no tracked .md file in `doc_repos` mentions."""
    log_dirs = ["/" + d.strip("/") + "/" for d in logs]
    text = []
    for r in doc_repos:
        if r.exists():
            for p in tracked(r, "*.md"):
                # skip docs inside a log folder; tracked but deleted: skip
                if not any(d in "/" + p for d in log_dirs + ["/test-logs/"]) and (r / p).is_file():
                    text.append((r / p).read_text(encoding="utf-8", errors="replace"))
    text = "\n".join(text)
    missing = []
    for p in tracked(repo, *logs):
        name = p.rsplit("/", 1)[-1]
        stem = name.rsplit(".", 1)[0]
        if not any(re.search(re.escape(n) + r"(?![\w-])", text) for n in {name, stem}):
            missing.append(p)
    return missing


def main(argv):
    repo = Path(argv[argv.index("--project") + 1] if "--project" in argv else ".").resolve()
    try:
        cfg = json.loads((repo / ".claude" / "habits.json").read_text(encoding="utf-8")).get("test_logs") or {}
    except (OSError, ValueError):
        cfg = {}
    logs = cfg.get("dirs") or []
    if not logs:
        print("check_test_logs: no habits.json test_logs.dirs, nothing to check", file=sys.stderr)
        return 0
    doc_repos = [repo] + [(repo / d).resolve() for d in cfg.get("doc_repos", [])]
    missing = unnamed(repo, logs, doc_repos)
    for p in missing:
        print(p)
    n = len(tracked(repo, *logs))
    print(f"check_test_logs: {n - len(missing)}/{n} files in {', '.join(logs)} named in a doc", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
