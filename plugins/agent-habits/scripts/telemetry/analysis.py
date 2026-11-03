"""Are the subagents helping? Per-run cost and size of every subagent, grouped by agent type and model.

Sources:
- Claude Code transcripts (~/.claude/projects/<project>/<session>.jsonl and <session>/subagents/agent-*.jsonl +
  .meta.json): agent type, description, model, per-call tokens, tool calls, wall time, the report it returned.
  Covers every past session, no setup needed. Caveat: transcripts store each request's usage from the stream
  start, so input / cache tokens are right but output tokens are far too low (a 7.7K-char report logged 20);
  use OTel for output tokens and cost.
- OTel logs from receiver.py (<telemetry>/*/logs.jsonl), when present: cost_usd and the real output tokens
  per API call, joined exactly on request_id (= the transcript's requestId, CLI and desktop alike). Transcripts
  don't carry cost.
- the verdicts CSV (hand-written after checking an agent's result in the main session): agent_id, verdict
  (correct / partly / wrong / unchecked), note. Correctness can't be measured from tokens.

Config: the project's .claude/habits.json "telemetry" (optional): "verdicts" (CSV path, default
.claude/agent-verdicts.csv), "projects" (more project folders whose sessions count, e.g. a sibling repo).

Outputs: <out>/agent_runs.csv (one row per subagent run) and a summary table on stdout.

    python -I analysis.py [--project <dir>] [--since 2026-10-01] [--projects <slug> ...] [--out <dir>]
"""
import argparse
import csv
import datetime
import json
import os
import re
import statistics
from collections import defaultdict
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"


def data_dir():
    return Path(os.environ.get("AGENT_TELEMETRY_DIR") or Path.home() / ".claude" / "agent-telemetry")


def project_slug(path):
    """Claude Code's folder name for a project: every non-alphanumeric character becomes `-`."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(path).resolve()))


def project_config(project):
    p = project / ".claude" / "habits.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("telemetry") or {}
    except (OSError, ValueError):
        return {}


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def usage_tokens(u):
    return {
        "input": u.get("input_tokens", 0) or 0,
        "cache_write": u.get("cache_creation_input_tokens", 0) or 0,
        "cache_read": u.get("cache_read_input_tokens", 0) or 0,
        "output": u.get("output_tokens", 0) or 0,
    }


def text_of(content):
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def subagent_run(jsonl, meta):
    """One subagent transcript -> totals. Requests are de-duplicated by requestId (one API call can be split
    over several assistant lines carrying the same usage)."""
    seen, tok, models, tools = set(), defaultdict(int), defaultdict(int), 0
    first = last = None
    report = ""
    for d in read_jsonl(jsonl):
        t = d.get("timestamp")
        if t:
            first = first or ts(t)
            last = ts(t)
        if d.get("type") != "assistant":
            continue
        m = d.get("message", {})
        uses = [b for b in m.get("content") or [] if isinstance(b, dict) and b.get("type") == "tool_use"]
        tools += len(uses)
        # Background agents hand their report back through the SubagentHandback tool, not as text.
        handback = [b["input"].get("message", "") for b in uses if b.get("name") == "SubagentHandback"]
        txt = handback[-1] if handback else text_of(m.get("content"))
        if txt.strip():
            report = txt
        rid = d.get("requestId") or d.get("uuid")
        if rid in seen or not m.get("usage"):
            continue
        seen.add(rid)
        models[m.get("model", "?")] += 1
        for k, v in usage_tokens(m["usage"]).items():
            tok[k] += v
    if not seen:
        return None
    return {
        "agent_type": meta.get("agentType", "?"),
        "description": meta.get("description", ""),
        "requested_model": meta.get("model", "default"),
        "model": max(models, key=models.get),
        "api_calls": len(seen),
        "tool_calls": tools,
        "minutes": round((last - first).total_seconds() / 60, 2) if first and last else "",
        "start": first.isoformat(timespec="seconds") if first else "",
        "end": last.isoformat(timespec="seconds") if last else "",
        **{f"tok_{k}": tok[k] for k in ("input", "cache_write", "cache_read", "output")},
        "tok_fresh": tok["input"] + tok["cache_write"] + tok["output"],
        "report_chars": len(report),
        "_request_ids": seen,
    }


def main_thread(jsonl):
    """Main-session totals (non-sidechain assistant lines) for the per-session comparison."""
    seen, tok = set(), defaultdict(int)
    model = defaultdict(int)
    for d in read_jsonl(jsonl):
        if d.get("type") != "assistant" or d.get("isSidechain"):
            continue
        m = d.get("message", {})
        rid = d.get("requestId") or d.get("uuid")
        if rid in seen or not m.get("usage"):
            continue
        seen.add(rid)
        model[m.get("model", "?")] += 1
        for k, v in usage_tokens(m["usage"]).items():
            tok[k] += v
    return {"api_calls": len(seen), "model": max(model, key=model.get) if model else "?",
            "tok_fresh": tok["input"] + tok["cache_write"] + tok["output"], "tok_cache_read": tok["cache_read"]}


def otel_costs(tel_dir):
    """api_request events -> by_rid {request_id: (cost_usd, output_tokens)} for subagent requests, main
    {session_id: cost_usd} for the rest (main thread, compaction, other auxiliary calls), and loose {session_id:
    [(time, model, agent name, agent_id, cost, output_tokens)]} for subagent requests without a request_id.
    A subagent request = agent_id set (CLI only; desktop 4.4212.0 sends none, tested 2026-11-01) or query_source
    "agent:..." (both)."""
    by_rid, main, loose = {}, defaultdict(float), defaultdict(list)
    for f in sorted(tel_dir.glob("*/logs.jsonl")):
        for rec in read_jsonl(f):
            for rl in rec.get("resourceLogs", []):
                for sl in rl.get("scopeLogs", []):
                    for lr in sl.get("logRecords", []):
                        a = {kv["key"]: next(iter(kv["value"].values()), None) for kv in lr.get("attributes", [])}
                        if a.get("event.name") not in ("api_request", "claude_code.api_request"):
                            continue
                        try:
                            c = float(a.get("cost_usd") or 0)
                            out = int(a.get("output_tokens") or 0)
                        except (TypeError, ValueError):
                            continue
                        sid = a.get("session.id")
                        if not (a.get("agent_id") or str(a.get("query_source", "")).startswith("agent:")):
                            main[sid] += c
                        elif a.get("request_id"):
                            by_rid[a["request_id"]] = (c, out)
                        else:
                            t = datetime.datetime.fromtimestamp(int(lr.get("timeUnixNano") or 0) / 1e9,
                                                                datetime.timezone.utc)
                            loose[sid].append((t, a.get("model"), a.get("agent.name"), a.get("agent_id"), c, out))
    return by_rid, main, loose


def add_cost(r, c, out):
    r["cost_usd"] = (r["cost_usd"] or 0) + c
    r["otel_output"] = (r["otel_output"] or 0) + out


def match_loose(runs, requests):
    """Fallback for subagent requests without a request_id: agent_id if it names a run, else the session's run of
    the same model whose transcript window holds the request (5 s before its first line to 30 s after its last),
    agent type breaking ties, a still-ambiguous request split evenly. Returns the cost left unmatched."""
    slack_pre, slack_post = datetime.timedelta(seconds=5), datetime.timedelta(seconds=30)
    left = 0.0
    for t, model, name, agent_id, c, out in requests:
        cand = [r for r in runs if agent_id and r["agent_id"] == agent_id]
        if not cand:
            cand = [r for r in runs if r["model"] == model and r["start"] and r["end"]
                    and ts(r["start"]) - slack_pre <= t <= ts(r["end"]) + slack_post]
            cand = [r for r in cand if name and r["agent_type"].split(":")[-1] == str(name).split(":")[-1]] or cand
        if not cand:
            left += c
            continue
        for r in cand:
            add_cost(r, c / len(cand), out / len(cand))
    return left


def load_verdicts(path):
    if not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as f:
        return {r["agent_id"]: r for r in csv.DictReader(f) if r.get("agent_id")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=Path("."), help="project folder (default: the current one)")
    ap.add_argument("--projects", nargs="*",
                    help="folder names under ~/.claude/projects (default: the project + habits.json telemetry.projects)")
    ap.add_argument("--since", help="YYYY-MM-DD; only sessions with activity from this date")
    ap.add_argument("--telemetry", type=Path, default=data_dir())
    ap.add_argument("--out", type=Path, default=data_dir() / "analysis")
    a = ap.parse_args()
    since = datetime.datetime.fromisoformat(a.since).replace(tzinfo=datetime.timezone.utc) if a.since else None
    project = a.project.resolve()
    cfg = project_config(project)
    if a.projects is None:
        a.projects = [project_slug(project)] + [project_slug(project / p) for p in cfg.get("projects", [])]

    verdicts = load_verdicts(project / cfg.get("verdicts", ".claude/agent-verdicts.csv"))
    by_rid, main_cost, loose = otel_costs(a.telemetry) if a.telemetry.exists() else ({}, {}, {})
    runs, sessions = [], []
    for proj in a.projects:
        pdir = PROJECTS_DIR / proj
        for sj in sorted(pdir.glob("*.jsonl")):
            sid = sj.stem
            sub_dir = pdir / sid / "subagents"
            mt = main_thread(sj)
            sess_runs, all_runs = [], []
            for aj in sorted(sub_dir.glob("agent-*.jsonl")) if sub_dir.exists() else []:
                meta_p = aj.with_suffix(".meta.json")
                meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
                r = subagent_run(aj, meta)
                if not r:
                    continue
                agent_id = aj.stem.removeprefix("agent-")
                v = verdicts.get(agent_id, {})
                rids = r.pop("_request_ids")
                r = {"project": proj.split("--")[-1], "session": sid, "agent_id": agent_id, **r,
                     "cost_usd": "", "otel_output": "", "otel_calls": 0, "verdict": v.get("verdict", "unchecked"), "verdict_note": v.get("note", "")}
                for rid in rids & by_rid.keys():
                    add_cost(r, *by_rid[rid])
                    r["otel_calls"] += 1
                all_runs.append(r)
                if not (since and r["start"] and ts(r["start"]) < since):
                    sess_runs.append(r)
            if since and not sess_runs and sj.stat().st_mtime < since.timestamp():
                continue
            # Match against every run of the session, so a run skipped by --since still takes its own requests
            # instead of leaving them as "unmatched"; only requests from --since on can warn.
            reqs = [q for q in loose.get(sid, []) if not since or q[0] >= since]
            unmatched = match_loose(all_runs, reqs)
            if unmatched:
                print(f"warning: session {sid}: ${unmatched:.4f} of subagent cost matched no run")
            for r in sess_runs:
                if r["cost_usd"] != "":
                    r["cost_usd"] = round(r["cost_usd"], 4)
                    r["otel_output"] = round(r["otel_output"])
            runs += sess_runs
            sessions.append({"session": sid, "main_fresh": mt["tok_fresh"], "main_cost": main_cost.get(sid),
                             "sub_fresh": sum(r["tok_fresh"] for r in sess_runs), "subagents": len(sess_runs),
                             "sub_cost": sum(r["cost_usd"] for r in sess_runs if r["cost_usd"] != "")})

    a.out.mkdir(parents=True, exist_ok=True)
    if runs:
        with open(a.out / "agent_runs.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(runs[0]))
            w.writeheader()
            w.writerows(runs)

    groups = defaultdict(list)
    for r in runs:
        groups[(r["agent_type"], r["model"].removeprefix("claude-"))].append(r)
    print(f"{len(runs)} subagent runs in {len(sessions)} sessions -> {a.out / 'agent_runs.csv'}")
    print("fresh = input + cache write + output tokens (cache reads are ~10x cheaper, shown apart); out* = "
          "transcript output tokens, undercounted (stream-start usage; the CSV's otel_output is the real count), "
          "OTel $ is the real cost; "
          "rpt = the agent's report in tokens (~chars/4), what the main session pays to read it; "
          "ok/chk = verdicts.csv correct / checked")
    hdr = f"{'agent type':28} {'model':12} {'runs':>4} {'min':>5} {'tools':>5} {'fresh':>7} {'c-read':>8} " \
          f"{'out*':>6} {'rpt':>5} {'ok/chk':>7} {'$ sum':>7} {'$ med':>6}"
    print(hdr + "\n" + "-" * len(hdr) + "\n" + " " * 46 + "(medians per run, except $ sum)")
    for (at, model), rs in sorted(groups.items(), key=lambda kv: -sum(r["tok_fresh"] for r in kv[1])):
        def med(k):
            vals = [float(r[k]) for r in rs if r[k] != ""]
            return statistics.median(vals) if vals else float("nan")
        checked = [r for r in rs if r["verdict"] != "unchecked"]
        ok = sum(r["verdict"] == "correct" for r in checked)
        costed = [r["cost_usd"] for r in rs if r["cost_usd"] != ""]
        print(f"{at[:28]:28} {model[:12]:12} {len(rs):>4} {med('minutes'):>5.1f} {med('tool_calls'):>5.0f} "
              f"{med('tok_fresh'):>7.0f} {med('tok_cache_read'):>8.0f} {med('tok_output'):>6.0f} "
              f"{med('report_chars') / 4:>5.0f} {f'{ok}/{len(checked)}':>7} "
              f"{f'{sum(costed):.2f}' if costed else '':>7} {f'{statistics.median(costed):.3f}' if costed else '':>6}")
    with_sub = [s for s in sessions if s["subagents"]]
    if with_sub:
        share = sum(s["sub_fresh"] for s in with_sub) / max(sum(s["main_fresh"] + s["sub_fresh"] for s in with_sub), 1)
        print(f"\nsessions using subagents: {len(with_sub)}/{len(sessions)}; subagent share of their fresh tokens: "
              f"{share:.0%}")
        costed = [s for s in with_sub if s["main_cost"] is not None]
        if costed:
            sub = sum(s["sub_cost"] for s in costed)
            print(f"  with OTel cost ({len(costed)} sessions): main ${sum(s['main_cost'] for s in costed):.2f}, "
                  f"subagents ${sub:.2f}")
    if not by_rid and not main_cost:
        print(f"no OTel cost data yet (receiver.py -> {a.telemetry}): $ columns empty")


if __name__ == "__main__":
    main()
