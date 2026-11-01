#!/usr/bin/env python3
"""
Per-agent use from the Claude Code transcripts: is each subagent type paying for itself?

    python -I agent_stats.py [--project <dir>] [--transcripts <dir> ...] [--all] [--since 2026-10-01]
                             [--window 15] [--detail] [--json]

Reads every subagent run (`<session>/subagents/*.meta.json` + its `.jsonl`) in the project's transcript folder
(~/.claude/projects/<slug>; --transcripts for others, --all for every project), and per agent type reports:
runs (background / foreground), model, tokens per run (fresh = input + cache writes + output; cache reads apart),
tool calls, minutes, answer size, and the re-read signal: after the answer reached the main session (tool result or
task notification), did the main session Read a file the agent had Read within its next --window tool calls? A
re-read means the main session redid (part of) the agent's work, or didn't trust its answer. Only Read calls are
compared (Grep / Bash reads aren't), so the count is a floor. --detail lists the re-read runs.
"""

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

DELIVERED = re.compile(r"<tool-use-id>(toolu_\w+)</tool-use-id>")


def project_slug(path):
    """Claude Code's folder name for a project: every non-alphanumeric character becomes `-`."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path.resolve()))


def norm(p):
    return str(p).replace("\\", "/").lower().rstrip("/")


def text_of(content):
    if isinstance(content, str):
        return content
    out = []
    for c in content or []:
        if isinstance(c, dict):
            if c.get("type") == "text":
                out.append(c.get("text", ""))
            elif c.get("type") == "tool_result":
                out.append(text_of(c.get("content")))
    return " ".join(out)


def lines(path):
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def agent_run(meta_path):
    """One subagent run: tokens, tool calls, files it Read, answer, start / end time."""
    m = json.loads(meta_path.read_text(encoding="utf-8"))
    run = {"type": m.get("agentType") or "?", "tool_use_id": m.get("toolUseId", ""),
           "description": m.get("description", ""), "shape": m.get("requestShape", "?"), "model": "",
           "fresh": 0, "cache_read": 0, "tools": 0, "reads": set(), "answer": "", "start": "", "end": ""}
    for o in lines(meta_path.with_name(meta_path.name.replace(".meta.json", ".jsonl"))):
        ts = o.get("timestamp", "")
        if ts:
            run["start"] = run["start"] or ts
            run["end"] = ts
        if o.get("type") != "assistant":
            continue
        msg = o.get("message") or {}
        run["model"] = msg.get("model") or run["model"]
        u = msg.get("usage") or {}
        run["fresh"] += sum(u.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "output_tokens"))
        run["cache_read"] += u.get("cache_read_input_tokens", 0)
        for c in msg.get("content") or []:
            if c.get("type") == "text":
                run["answer"] = c["text"]
            elif c.get("type") == "tool_use":
                inp = c.get("input") or {}
                if c.get("name") in ("SubagentHandback", "SendMessage") and "message" in inp:
                    run["answer"] = str(inp["message"])   # the hand-back of a background run
                    continue
                run["tools"] += 1
                if c.get("name") == "Read" and inp.get("file_path"):
                    run["reads"].add(norm(inp["file_path"]))
    return run


def main_calls(session_jsonl):
    """The main session in order: ('deliver', tool_use_id) when an agent answer arrives, ('read', path) / ('tool',)
    for the main session's own tool calls."""
    events = []
    for o in lines(session_jsonl):
        if o.get("isSidechain"):
            continue
        t = o.get("type")
        msg = o.get("message") or {}
        if t == "assistant":
            for c in msg.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    inp = c.get("input") or {}
                    events.append(("read", norm(inp["file_path"])) if c.get("name") == "Read" and
                                  inp.get("file_path") else ("tool", ""))
        elif t == "user":
            content = msg.get("content")
            for c in content if isinstance(content, list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_result" and \
                        "Async agent launched" not in text_of(c.get("content")):
                    events.append(("deliver", c.get("tool_use_id", "")))
            for tid in DELIVERED.findall(text_of(content)):
                events.append(("deliver", tid))
        elif t == "attachment":
            for tid in DELIVERED.findall(json.dumps(o.get("attachment") or {})):
                events.append(("deliver", tid))
    return events


def minutes(a, b):
    from datetime import datetime
    try:
        f = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (f(b) - f(a)).total_seconds() / 60
    except ValueError:
        return 0.0


def collect(dirs, since="", window=15):
    runs = []
    for d in dirs:
        for meta in sorted(d.glob("*/subagents/*.meta.json")):
            try:
                run = agent_run(meta)
            except (OSError, ValueError):
                continue
            if run["start"][:10] < since:
                continue
            session = meta.parent.parent
            run["session"] = session.name
            run["project"] = d.name
            run["rereads"] = []
            run["delivered"] = False
            events = main_calls(session.with_suffix(".jsonl")) if run["reads"] else []
            for i, (kind, val) in enumerate(events):
                if kind == "deliver" and val == run["tool_use_id"]:
                    run["delivered"] = True
                    after = [v for k, v in events[i + 1:] if k != "deliver"][:window]
                    run["rereads"] = sorted({v for v in after if v in run["reads"]})
                    break
            runs.append(run)
    return runs


def summarize(runs):
    by = defaultdict(list)
    for r in runs:
        by[r["type"]].append(r)
    out = []
    for t, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        med = lambda k: statistics.median(r[k] for r in rs)
        with_reads = [r for r in rs if r["reads"] and r["delivered"]]
        models = sorted({r["model"].replace("claude-", "") for r in rs if r["model"]})
        out.append({
            "type": t, "runs": len(rs), "background": sum(r["shape"] == "background" for r in rs),
            "models": models,
            "fresh_median": med("fresh"), "cache_read_median": med("cache_read"),
            "fresh_total": sum(r["fresh"] for r in rs), "cache_read_total": sum(r["cache_read"] for r in rs),
            "tools_median": med("tools"),
            "minutes_median": statistics.median(minutes(r["start"], r["end"]) for r in rs),
            "answer_chars_median": statistics.median(len(r["answer"]) for r in rs),
            "reread_runs": sum(bool(r["rereads"]) for r in with_reads), "runs_with_reads": len(with_reads),
        })
    return out


def k(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}K"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    ap.add_argument("--transcripts", nargs="*", default=[])
    ap.add_argument("--all", action="store_true", help="every project folder under ~/.claude/projects")
    ap.add_argument("--since", default="")
    ap.add_argument("--window", type=int, default=15, help="main-session tool calls checked for re-reads")
    ap.add_argument("--detail", action="store_true", help="list the runs whose files the main session re-read")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = Path.home() / ".claude" / "projects"
    dirs = [p for p in root.iterdir() if p.is_dir()] if a.all else \
        [Path(d).expanduser() for d in a.transcripts] or [root / project_slug(Path(a.project))]
    runs = collect(dirs, a.since, a.window)
    rows = summarize(runs)
    if a.json:
        print(json.dumps({"runs": len(runs), "agents": rows}, indent=1))
        return 0
    if not runs:
        print("No subagent runs found in: " + ", ".join(str(d) for d in dirs))
        return 0
    first = min(r["start"][:10] for r in runs)
    print(f"{len(runs)} subagent runs since {a.since or first} in {len(dirs)} transcript folder(s). "
          f"Tokens per run are medians; fresh = input + cache writes + output, cache reads apart.\n")
    print("| Agent | Runs (bg) | Model | Fresh / run | Cache reads / run | Total fresh / cache | Tools | Min "
          f"| Answer chars | Re-read in {a.window} calls |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['type']} | {r['runs']} ({r['background']}) | {', '.join(r['models'])} | {k(r['fresh_median'])} "
              f"| {k(r['cache_read_median'])} | {k(r['fresh_total'])} / {k(r['cache_read_total'])} "
              f"| {r['tools_median']:.0f} | {r['minutes_median']:.1f} | {r['answer_chars_median']:.0f} "
              f"| {r['reread_runs']} / {r['runs_with_reads']} |")
    print("\nRe-read: runs whose Read files the main session Read again soon after the answer, out of runs that Read "
          "files and whose answer was found in the main transcript. Grep / Bash reads aren't compared (a floor).")
    if a.detail:
        print("\n## Re-read runs")
        for r in runs:
            if r["rereads"]:
                print(f"- {r['type']} \"{r['description']}\" ({r['start'][:10]}, {r['session'][:8]}): "
                      + ", ".join(Path(p).name for p in r["rereads"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
