#!/usr/bin/env python3
"""
Mechanical checks on a project's knowledge base (the librarian's Audit mode runs this, then judges the rest).

    python -I kb_audit.py --project <dir> [--json]

Reads <project>/.claude/habits.json "knowledge_audit":
    files       files / folders of .md to check (relative to the project, absolute, or ~)
    code_roots  folders searched for the names an entry cites in its "Used in" line, and for cited paths
    repos       git repos whose commits may be cited (default: the project)
Checks, per file: relative links to missing files or anchors, backticked paths that exist nowhere, "Used in" names
found in no code root, commit hashes no repo resolves, duplicate titles / anchors across files, and memory `[[name]]`
links with no memory of that name. Prints a markdown list (or JSON); exit 0 even with findings.
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
ANCHOR_TAG = re.compile(r"<a\s+(?:id|name)=[\"']([^\"']+)[\"']", re.I)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.M)
TICKED = re.compile(r"`([^`\n]+)`")
PATHLIKE = re.compile(r"^[\w.~-]+(?:[/\\][\w.@ -]+)*\.(?:py|lua|md|json|csv|txt|ps1|cs|csproj|asm|ini|yaml|yml|toml|sh)$",
                      re.I)
HASH = re.compile(r"(?<![\w/#-])([0-9a-f]{7,12})(?![\w-])")
GC_ADDRESS = re.compile(r"^[89][01][0-9a-f]{6}$")   # GameCube / Wii RAM addresses look like short hashes
USED_IN = re.compile(r"\*\*Used in:?\*\*:?(.*)", re.I)
IDENT = re.compile(r"^[A-Za-z_][\w.:]*(?:\(\))?$")
WIKILINK = re.compile(r"\[\[([^\]|#]+)")


def slug(title):
    """GitHub-style heading anchor."""
    t = re.sub(r"<[^>]+>", "", title).strip().lower()
    t = re.sub(r"[^\w\- ]", "", t)
    return t.replace(" ", "-")


def resolve(base, p):
    q = Path(p).expanduser()
    return q if q.is_absolute() else base / q


def md_files(base, entries):
    out = []
    for e in entries:
        p = resolve(base, e)
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out += sorted(f for f in p.rglob("*.md") if ".git" not in f.parts)
    return out


def anchors(path, cache={}):
    if path not in cache:
        text = path.read_text(encoding="utf-8", errors="replace")
        ids = set(ANCHOR_TAG.findall(text))
        seen = defaultdict(int)
        for _, title in HEADING.findall(text):
            s = slug(title)
            ids.add(s if not seen[s] else f"{s}-{seen[s]}")
            seen[s] += 1
        cache[path] = ids
    return cache[path]


def tracked_files(roots):
    """Every git-tracked file under the code roots, as forward-slash paths relative to their root."""
    out = []
    for root in roots:
        r = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True, encoding="utf-8")
        out += [(root, p) for p in r.stdout.splitlines()]
    return out


def path_verdict(tok, bases, tracked, roots):
    """("", "") = exists; (check, note) = a finding; None = names something outside the code roots (not checked)."""
    if any((b / tok).exists() for b in bases):
        return "", ""
    if any(p == tok or p.endswith("/" + tok) for _, p in tracked):
        return "", ""   # cited relative to a deeper folder
    name = tok.rsplit("/", 1)[-1]
    moved = sorted({f"{root.name}/{p}" for root, p in tracked if p.rsplit("/", 1)[-1] == name})
    if moved:
        return "moved path?", f" -> now {', '.join(moved[:3])}" + (" ..." if len(moved) > 3 else "")
    first = tok.split("/", 1)[0]
    tops = {root.name for root in roots} | {p.split("/", 1)[0] for _, p in tracked if "/" in p}
    return ("missing path", "") if first in tops else None


def git_has(repo, sha):
    r = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", sha + "^{commit}"], capture_output=True)
    return r.returncode == 0


def code_has(roots, name):
    for root in roots:
        r = subprocess.run(["git", "-C", str(root), "grep", "-q", "-F", "-e", name, "--", ".", ":!*.md"],
                           capture_output=True)   # code only: the docs citing the name don't count
        if r.returncode == 0:
            return True
    return False


def audit(project):
    cfg = json.loads((project / ".claude" / "habits.json").read_text(encoding="utf-8"))
    ka = cfg.get("knowledge_audit") or {}
    report = resolve(project, ka["report"]).resolve() if ka.get("report") else None
    files = [f for f in md_files(project, ka.get("files") or []) if f.resolve() != report]
    roots = [resolve(project, r) for r in ka.get("code_roots") or []]
    repos = [resolve(project, r) for r in ka.get("repos") or ["."]]
    found = defaultdict(list)   # check -> [(file, line, detail)]
    titles, ids = defaultdict(list), defaultdict(list)
    skipped = defaultdict(int)
    hash_ok = {}
    tracked = tracked_files(roots)

    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        in_code = False
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("```"):
                in_code = not in_code
            if in_code or line.startswith("    "):
                continue
            where = (f, n)
            for target in LINK.findall(line):
                if re.match(r"^[a-z]+:", target, re.I):
                    continue
                file_part, _, frag = target.partition("#")
                dest = (f.parent / file_part).resolve() if file_part else f
                if not dest.exists():
                    found["broken link"].append((*where, target))
                elif frag and dest.suffix == ".md" and frag not in anchors(dest):
                    found["missing anchor"].append((*where, target))
            for tok in TICKED.findall(line):
                tok = re.sub(r":[\d,-]+$", "", tok.strip()).replace("\\", "/")
                if PATHLIKE.match(tok) and "/" in tok:
                    verdict = path_verdict(tok, [*f.parents, project, *roots, *(r.parent for r in roots)],
                                           tracked, roots)
                    if verdict is None:
                        skipped["paths outside the code roots"] += 1
                    elif verdict[0]:
                        found[verdict[0]].append((*where, tok + verdict[1]))
            m = USED_IN.search(line)
            if m and roots:
                for tok in TICKED.findall(m.group(1)):
                    name = tok.strip().rstrip("()")
                    is_file = re.search(r"\.(py|lua|md|json|csv|txt|cs)$", name, re.I)
                    if IDENT.match(tok.strip()) and not is_file and len(name) > 3:
                        if not code_has(roots, name):
                            found["Used in name not in code"].append((*where, name))
            for sha in HASH.findall(line):
                if not (re.search(r"[a-f]", sha) and re.search(r"\d", sha)) or GC_ADDRESS.match(sha):
                    continue
                if sha not in hash_ok:
                    hash_ok[sha] = any(git_has(r, sha) for r in repos)
                if not hash_ok[sha]:
                    found["unknown commit"].append((*where, sha))
        for _, title in HEADING.findall(text):
            titles[title.strip().lower()].append(f)
        for a in ANCHOR_TAG.findall(text):
            ids[a].append(f)
        if f.parent.name == "memory":
            names = {re.search(r"^name:\s*(\S+)", p.read_text(encoding="utf-8", errors="replace"), re.M)
                     for p in f.parent.glob("*.md")}
            names = {m.group(1) for m in names if m}
            for n, line in enumerate(text.splitlines(), 1):
                for w in WIKILINK.findall(line):
                    if w.strip() not in names:
                        found["memory link, no memory yet (allowed: one worth writing?)"].append((f, n, w.strip()))

    for t, fs in titles.items():
        if len(fs) > 1 and len(t) > 12:
            found["duplicate title"].append((fs[0], 0, f"'{t}' in {', '.join(sorted({p.name for p in fs}))}"))
    for a, fs in ids.items():
        if len(fs) > 1:
            found["duplicate anchor"].append((fs[0], 0, f"{a} x{len(fs)}"))
    return files, found, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    project = Path(a.project).resolve()
    files, found, skipped = audit(project)

    def rel(p):
        try:
            return p.relative_to(project).as_posix()
        except ValueError:
            return p.as_posix()
    if a.json:
        print(json.dumps({k: [{"file": rel(f), "line": n, "detail": d} for f, n, d in v] for k, v in found.items()},
                         indent=1))
        return
    print(f"kb_audit: {len(files)} files, " + (", ".join(f"{k} {len(v)}" for k, v in found.items()) or "no findings")
          + "".join(f"; not checked: {k} {n}" for k, n in skipped.items()))
    for k, v in found.items():
        print(f"\n## {k} ({len(v)})")
        for f, n, d in v:
            print(f"- {rel(f)}{':' + str(n) if n else ''}: `{d}`")


if __name__ == "__main__":
    sys.exit(main())
