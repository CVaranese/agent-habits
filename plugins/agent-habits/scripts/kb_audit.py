#!/usr/bin/env python3
"""
Mechanical checks on a project's knowledge base (the librarian's Audit mode runs this, then judges the rest).

    python -I kb_audit.py --project <dir> [--json]
    python -I kb_audit.py --project <dir> --index      # only rewrite the entry index (fast; after File mode)
    python -I kb_audit.py --project <dir> --use [--since 2026-10-01]   # only the librarian-use summary

Reads <project>/.claude/habits.json "knowledge_audit":
    files       files / folders of .md to check (relative to the project, absolute, or ~)
    code_roots  folders searched for the names an entry cites in its "Used in" line, and for cited paths
    repos       git repos whose commits may be cited (default: the project)
    index       optional: file for the entry index, one line per entry (file#anchor, line, status, title, Used in):
                Find mode greps it first instead of the whole base. Written on every audit run and by --index.
    transcripts optional: Claude Code project folders (~/.claude/projects/<slug>) whose librarian runs are counted;
                default: the folders of the project and the code roots
    leads       optional: files / folders (among `files`) that are lists of leads, not facts: their links, paths and
                commits are still checked, but they stay out of the index and have no entry format
    entry_max_chars  optional: an entry longer than this is reported (default 3000; 0 = no limit)
Checks, per file: relative links to missing files or anchors, backticked paths that exist nowhere, "Used in" names
found in no code root (with the code names they were probably renamed to), commit hashes no repo resolves, duplicate
titles / anchors across files, and memory `[[name]]` links with no memory of that name. Entry format, for every
entry with a Status line: a Status that is more than one word with an optional date and short bracket, no Evidence
line, an entry over the size limit, and the project's own code cited by line number. Then the librarian's use:
runs per mode, tokens, and Find results (the `Result: hit | partial | miss` line Find answers end with). Prints a
markdown list (or JSON); exit 0 even with findings.
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

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
STATUS = re.compile(r"\*\*Status:?\*\*:?\s*([^\n.;(]+)", re.I)
CODE_TOKEN = r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*"
FIND_RESULT = re.compile(r"Result:\**\s*(hit|partial|miss)\b", re.I)
STATUS_LINE = re.compile(r"\*\*Status:?\*\*:?\s*(.*)", re.I)
# "Tested", "Tested 2026-11-02", "Tested (2026-11-11, H-75)": one word, an optional date, one short bracket
STATUS_CLEAN = re.compile(r"^[A-Za-z][A-Za-z-]*(\s+\d{4}-\d\d-\d\d)?(\s*\([^()]{1,40}\))?\.?$")
EVIDENCE = re.compile(r"\*\*Evidence\b", re.I)
CODE_EXT = r"\.(?:py|lua|cs|js|ts|c|cc|cpp|h|rs|go|java|rb|sh|ps1)"
# `convert.py:62`, `src/a.lua:10-14`, and `convert.py` (~L62, ...)
LINE_CITE = re.compile(r"`([^`\n]*?" + CODE_EXT + r")(?::\d[\d,-]*`|`\s*\(~?L\d)", re.I)
FORMAT_CHECKS = ("status carries prose", "no Evidence line", "long entry", "code cited by line number")
LIST_CAP = 20   # format findings listed per check in the markdown output (--json lists all)


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
    """Git-tracked files under the code roots: ({file name: [(root, relative path)]}, top-level names)."""
    by_name, tops = defaultdict(list), {root.name for root in roots}
    for root in roots:
        r = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, text=True,
                           encoding="utf-8")   # -z: non-ASCII names unquoted
        for p in r.stdout.split("\0"):
            if p:
                by_name[p.rsplit("/", 1)[-1]].append((root, p))
                tops.add(p.split("/", 1)[0])
    return by_name, tops


def path_verdict(tok, bases, tracked):
    """("", "") = exists; (check, note) = a finding; None = names something outside the code roots (not checked)."""
    if any((b / tok).exists() for b in bases):
        return "", ""
    by_name, tops = tracked
    same_name = by_name.get(tok.rsplit("/", 1)[-1], [])
    if any(p == tok or p.endswith("/" + tok) for _, p in same_name):
        return "", ""   # cited relative to a deeper folder
    moved = sorted({f"{root.name}/{p}" for root, p in same_name})
    if moved:
        return "moved path?", f" -> now {', '.join(moved[:3])}" + (" ..." if len(moved) > 3 else "")
    return ("missing path", "") if tok.split("/", 1)[0] in tops else None


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


def code_tokens(roots):
    """Every identifier (dotted: `WALK.animation`) in the code roots' tracked non-markdown files."""
    out = set()
    for root in roots:
        r = subprocess.run(["git", "-C", str(root), "grep", "-I", "-h", "-o", "-E", CODE_TOKEN, "--", ".", ":!*.md"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out.update(r.stdout.split())
    return out


def name_parts(name):
    """`SI_AD_TIMER0` -> ['SI', 'AD', 'TIMER0'], `walkAnimation` -> ['walk', 'Animation']."""
    return [p for p in re.split(r"[_.:]|(?<=[a-z0-9])(?=[A-Z])", name) if p]


def rename_candidates(name, tokens):
    """Code names a stale "Used in" name was probably renamed to: one part dropped (`AD_SPEED` -> `SPEED`,
    `SI_AD_TIMER0` -> `SI_TIMER0`), or a camelCase name turned into a table field (`walkAnimation` ->
    `WALK.animation`). A guess by spelling: the librarian confirms it in the code before fixing."""
    parts = name_parts(name)
    snake = "_" in name
    joined = (lambda ps: "_".join(ps)) if snake else (lambda ps: ps[0][:1].lower() + ps[0][1:] + "".join(
        p[:1].upper() + p[1:] for p in ps[1:]))
    if len(parts) < 2:
        return []
    tail = joined(parts[1:])
    table = parts[0].upper() + "." + (tail if snake else tail[:1].lower() + tail[1:])   # WALK.ANIMS, WALK.animation
    if table in tokens:
        return [table]   # a section table's field: the clearest sign of a rename
    variants = set()
    for i in range(len(parts)):
        rest = parts[:i] + parts[i + 1:]
        # one word left: only a constant losing its prefix (`AD_SPEED` -> `SPEED`); `knee` or `start` say nothing
        if len(rest) > 1 or (snake and i == 0 and len(rest[0]) >= 5):
            variants.add(joined(rest))
    return sorted(v for v in variants if v != name and v in tokens)


def code_files(roots, name):
    """Files (root/relative) whose code names `name` as a whole word."""
    out = []
    for root in roots:
        r = subprocess.run(["git", "-C", str(root), "grep", "-l", "-w", "-F", "-e", name, "--", ".", ":!*.md"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out += [f"{root.name}/{p}" for p in r.stdout.split("\n") if p]
    return out


def entries(f, text):
    """(line, anchor, title, status, used-in names) per entry: an `<a id>` tag with the heading after it, or a
    heading of level 2+ with no tag; a memory file is one entry (its name and description)."""
    lines = text.splitlines()
    if f.parent.name == "memory" and lines[:1] == ["---"]:
        name = re.search(r"^name:\s*(.+)$", text, re.M)
        desc = re.search(r"^description:\s*(.+)$", text, re.M)
        return [(1, "", (name.group(1) if name else f.stem) + (": " + desc.group(1) if desc else ""), "", [])]
    out, pending = [], None
    for n, line in enumerate(lines, 1):
        tag = ANCHOR_TAG.search(line)
        if tag:
            pending = (n, tag.group(1))
            continue
        h = re.match(r"^(#{2,6})\s+(.+?)\s*#*\s*$", line)
        if h:
            n0, anchor = pending if pending else (n, slug(h.group(2)))
            out.append([n0, anchor, h.group(2), "", []])
            pending = None
        elif out:
            s = STATUS.search(line)
            if s and not out[-1][3]:
                out[-1][3] = s.group(1).strip()
            u = USED_IN.search(line)
            if u:
                out[-1][4] += [t.strip() for t in TICKED.findall(u.group(1))][:6]
    return [tuple(e) for e in out]


def kb_files(project, ka):
    """(files to check, the leads among them): the generated report and index are left out."""
    generated = {resolve(project, ka[k]).resolve() for k in ("report", "index") if ka.get(k)}
    files = [f for f in md_files(project, ka.get("files") or []) if f.resolve() not in generated]
    leads = {f.resolve() for f in md_files(project, ka.get("leads") or [])}
    return files, leads


def format_findings(text, max_chars=3000, own_code=()):
    """[(check, line, detail)] for every entry (heading of level 2+ up to the next one) that has a Status line."""
    lines = text.splitlines()
    heads = [i for i, l in enumerate(lines) if re.match(r"^#{2,6}\s+\S", l)]
    out = []
    for k, i in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        while end > i + 1 and (not lines[end - 1].strip() or ANCHOR_TAG.search(lines[end - 1])):
            end -= 1   # the next entry's anchor tag and the blank lines before it
        body = lines[i:end]
        status = next((j for j, l in enumerate(body) if STATUS_LINE.search(l)), None)
        if status is None:
            continue
        title = re.sub(r"^#+\s+", "", lines[i])[:60]
        value = STATUS_LINE.search(body[status]).group(1).strip()
        wraps = status + 1 < len(body) and re.match(r"\s+\S", body[status + 1]) \
            and not re.match(r"\s*[-*+]\s", body[status + 1])
        if not STATUS_CLEAN.match(value) or wraps:
            out.append(("status carries prose", i + 1, f"{title}: {value[:50]}"))
        if not any(EVIDENCE.search(l) for l in body):
            out.append(("no Evidence line", i + 1, title))
        size = sum(len(l) + 1 for l in body)
        if max_chars and size > max_chars:
            out.append(("long entry", i + 1, f"{title}: {size} chars"))
        cited = sorted({m.group(1).replace("\\", "/").rsplit("/", 1)[-1] for l in body for m in LINE_CITE.finditer(l)})
        cited = [c for c in cited if not own_code or c in own_code]
        if cited:
            out.append(("code cited by line number", i + 1, f"{title}: {', '.join(cited)}"))
    return out


def write_index(project, files, path):
    def rel(p):
        try:
            return p.relative_to(path.parent).as_posix()
        except ValueError:
            return p.as_posix()
    rows = []
    for f in files:
        for n, anchor, title, status, used in entries(f, f.read_text(encoding="utf-8", errors="replace")):
            rows.append(f"{rel(f)}{'#' + anchor if anchor else ''} L{n} [{status or '-'}] {title}"
                        + (f" | used in: {', '.join(used)}" if used else ""))
    path.write_text("# Knowledge index (generated by kb_audit.py; don't edit)\n"
                    "# file#anchor  line  [status]  title | used in\n" + "\n".join(rows) + "\n", encoding="utf-8",
                    newline="\n")
    return len(rows)


def project_slug(path):
    """Claude Code's folder name for a project: every non-alphanumeric character becomes `-`."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path.resolve()))


def librarian_use(project, ka, roots, since=""):
    """Librarian runs found in the Claude Code transcripts: per mode count and tokens, and Find results."""
    dirs = [Path(d).expanduser() for d in ka.get("transcripts") or []] or \
        [Path.home() / ".claude" / "projects" / project_slug(p) for p in {project, *roots}]
    runs = []
    for d in dirs:
        for meta in d.glob("*/subagents/*.meta.json"):
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not str(m.get("agentType", "")).endswith("librarian"):
                continue
            mode, date, tokens, answer = "", "", 0, ""
            log = meta.with_name(meta.name.replace(".meta.json", ".jsonl"))
            if not log.is_file():
                continue   # a run whose transcript was never written or was cleaned up
            for line in log.open(encoding="utf-8", errors="replace"):
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                msg = o.get("message") or {}
                if o.get("type") == "user" and not mode:
                    c = msg.get("content")
                    c = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                    first = re.match(r"\s*(\w+)", c or "")
                    mode = first.group(1).capitalize() if first else "?"
                    date = o.get("timestamp", "")[:10]
                if o.get("type") == "assistant":
                    u = msg.get("usage") or {}
                    tokens += sum(u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                        "cache_creation_input_tokens", "output_tokens"))
                    for c in msg.get("content") or []:
                        if c.get("type") == "text":
                            answer = c["text"]
                        elif c.get("type") == "tool_use" and "message" in (c.get("input") or {}):
                            answer = str(c["input"]["message"])   # the hand-back of a background run
            if date >= since:
                r = FIND_RESULT.search(answer)
                runs.append({"mode": mode if mode in ("File", "Find", "Audit") else "other", "date": date,
                             "tokens": tokens, "result": r.group(1).lower() if r else "unmarked"})
    use = defaultdict(lambda: {"runs": 0, "tokens": 0})
    for r in runs:
        use[r["mode"]]["runs"] += 1
        use[r["mode"]]["tokens"] += r["tokens"]
    finds = defaultdict(int)
    for r in runs:
        if r["mode"] == "Find":
            finds[r["result"]] += 1
    first = min((r["date"] for r in runs), default="")
    return {"since": since or first, "modes": dict(use), "find_results": dict(finds)}


def use_lines(u):
    if not u["modes"]:
        return ["No librarian runs found in the transcripts."]
    out = [f"Since {u['since']}: " + ", ".join(f"{m} {v['runs']} runs ({v['tokens'] / 1e6:.1f}M tokens)"
                                              for m, v in sorted(u["modes"].items()))]
    if u["find_results"]:
        out.append("Find results: " + ", ".join(f"{k} {v}" for k, v in sorted(u["find_results"].items()))
                   + " (hit = the base answered it; unmarked = answered before the Result line existed)")
    return out


def audit(project):
    cfg = json.loads((project / ".claude" / "habits.json").read_text(encoding="utf-8"))
    ka = cfg.get("knowledge_audit") or {}
    files, leads = kb_files(project, ka)
    roots = [resolve(project, r) for r in ka.get("code_roots") or []]
    repos = [resolve(project, r) for r in ka.get("repos") or ["."]]
    max_chars = ka.get("entry_max_chars", 3000)
    found = defaultdict(list)   # check -> [(file, line, detail)]
    titles, ids = defaultdict(list), defaultdict(list)
    skipped = defaultdict(int)
    hash_ok, code_ok, memory_names, renamed = {}, {}, {}, {}
    tokens = None   # every code name, read once and only when a "Used in" name is missing
    tracked = tracked_files(roots)

    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        if f.resolve() not in leads and f.parent.name != "memory":
            for check, n, detail in format_findings(text, max_chars, tracked[0]):
                found[check].append((f, n, detail))
        in_code = indented = False
        prev = ""
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("```"):
                in_code = not in_code
            # indented code block: 4+ spaces after a blank line (or more of it); nested list items aren't code
            indented = (line.startswith("    ") and (not prev.strip() or indented)
                        and not re.match(r"\s*([-*+]|\d+\.)\s", line))
            prev = line
            if in_code or indented:
                continue
            where = (f, n)
            for target in LINK.findall(line):
                if re.match(r"^[a-z]+:", target, re.I):
                    continue
                file_part, _, frag = target.partition("#")
                dest = (f.parent / unquote(file_part)).resolve() if file_part else f
                if not dest.exists():
                    found["broken link"].append((*where, target))
                elif frag and dest.suffix == ".md" and frag not in anchors(dest):
                    found["missing anchor"].append((*where, target))
            for tok in TICKED.findall(line):
                tok = re.sub(r":[\d,-]+$", "", tok.strip()).replace("\\", "/")
                if PATHLIKE.match(tok) and "/" in tok:
                    verdict = path_verdict(tok, [*f.parents, project, *roots, *(r.parent for r in roots)], tracked)
                    if verdict is None:
                        skipped["paths outside the code roots"] += 1
                    elif verdict[0]:
                        found[verdict[0]].append((*where, tok + verdict[1]))
            m = USED_IN.search(line)
            if m and roots:
                for tok in TICKED.findall(m.group(1)):
                    name = tok.strip().rstrip("()")
                    is_file = re.search(r"\.(py|lua|md|json|csv|txt|cs)$", name, re.I)
                    if not IDENT.match(tok.strip()) or is_file:
                        continue
                    last = re.split(r"[.:]", name)[-1]   # `Class:method`, `TABLE.field`: the member is the code
                    if len(last) > 3:
                        if last not in code_ok:
                            code_ok[last] = code_has(roots, last)
                        if not code_ok[last]:
                            if tokens is None:
                                tokens = code_tokens(roots)
                            if name not in renamed:
                                cands = rename_candidates(name, tokens)
                                renamed[name] = (f" -> renamed? {cands[0]} in {', '.join(code_files(roots, cands[0])[:3])}"
                                                 if len(cands) == 1 else
                                                 f" -> renamed? one of {', '.join(cands[:4])}" if cands else "")
                            found["Used in name not in code"].append((*where, name + renamed[name]))
            for sha in HASH.findall(line):
                if not (re.search(r"[a-f]", sha) and re.search(r"\d", sha)) or GC_ADDRESS.match(sha):
                    continue
                if sha not in hash_ok:
                    hash_ok[sha] = any(git_has(r, sha) for r in repos)
                if not hash_ok[sha]:
                    found["unknown commit"].append((*where, sha))
        if f.resolve() not in leads:   # a lead may repeat a fact's title
            for _, title in HEADING.findall(text):
                titles[title.strip().lower()].append(f)
        for a in ANCHOR_TAG.findall(text):
            ids[a].append(f)
        if f.parent.name == "memory":
            if f.parent not in memory_names:
                found_names = (re.search(r"^name:\s*(\S+)", p.read_text(encoding="utf-8", errors="replace"), re.M)
                               for p in f.parent.glob("*.md"))
                memory_names[f.parent] = {m.group(1) for m in found_names if m}
            names = memory_names[f.parent]
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
    return [f for f in files if f.resolve() not in leads], found, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--index", action="store_true", help="only rewrite knowledge_audit.index")
    ap.add_argument("--use", action="store_true", help="only the librarian-use summary")
    ap.add_argument("--since", default="", help="librarian use from this date (YYYY-MM-DD)")
    a = ap.parse_args()
    project = Path(a.project).resolve()
    ka = json.loads((project / ".claude" / "habits.json").read_text(encoding="utf-8")).get("knowledge_audit") or {}
    roots = [resolve(project, r) for r in ka.get("code_roots") or []]
    index = resolve(project, ka["index"]) if ka.get("index") else None
    if a.index or a.use:
        if a.index:
            if not index:
                print("kb_audit: no knowledge_audit.index in habits.json")
                return 1
            files, leads = kb_files(project, ka)
            files = [f for f in files if f.resolve() not in leads]
            print(f"kb_audit: index {index} ({write_index(project, files, index)} entries)")
        if a.use:
            print("\n".join(use_lines(librarian_use(project, ka, roots, a.since))))
        return 0
    files, found, skipped = audit(project)
    use = librarian_use(project, ka, roots, a.since)
    n_index = write_index(project, files, index) if index else 0

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
          + "".join(f"; not checked: {k} {n}" for k, n in skipped.items())
          + (f"; index rewritten ({n_index} entries)" if index else ""))
    for k, v in found.items():
        print(f"\n## {k} ({len(v)})")
        shown = v[:LIST_CAP] if k in FORMAT_CHECKS else v
        for f, n, d in shown:
            print(f"- {rel(f)}{':' + str(n) if n else ''}: `{d}`")
        if len(shown) < len(v):
            print(f"- ... and {len(v) - len(shown)} more (--json lists all)")
    print("\n## Librarian use\n" + "\n".join(f"- {x}" for x in use_lines(use)))


if __name__ == "__main__":
    sys.exit(main())
