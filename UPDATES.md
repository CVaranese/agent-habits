# Updates log

(Routine bookkeeping — last check, changelog version, docs pages seen — lives in state.json on the `habits-state` branch.)

## 2026-11-02 — reply-length telemetry (v0.10.1)
Source: a user found the main session's replies too long and many turns driven by subagent reports rather than by
them. `agent_stats.py --replies` counts, per main-session turn, who started it (a human message, or an agent:
subagent hand-back, task notification, another session) and the words in the turn's final reply; it prints median,
90th percentile and turns over 200 words for each. On the source project since 2026-11-01: 364 turns, 175
agent-started; user turns median 119 words (p90 433), agent turns median 34 (p90 352). `test_habits.py` 1 new check.

## 2026-11-02 — review line on every close, verdict reminder (v0.10.0)
Source: two prose rules that kept slipping on one project: "record the /code-review in the close" (of 17 closes in
two days, 2 had no review line, and several said "no /code-review result was handed in") and "a verdict row per
subagent run". Now checks: `tasks.py --check` with `tasks.review_since` fails a close from that date whose Resolution
has no `Review:` / `Review findings:` line (`Review: none (<why>)` is fine; the scheduler writes it); the Stop hook,
with a `"telemetry"` config, names this session's finished subagent runs missing from the verdicts CSV, once each
(runs written to in the last 30 s count as still running). `test_habits.py` covers both (5 new checks).

## 2026-11-02 — telemetry, leases, test-log check, daily review (v0.9.0)
Source: the rest of the admin machinery one project had built for itself, moved here so any project gets it. Ported
with project paths turned into config: `scripts/telemetry/` (OTLP receiver, started by the plugin's SessionStart hook
only when telemetry is on and points at localhost; `analysis.py` reads `"telemetry"` from habits.json; data in
`$AGENT_TELEMETRY_DIR` or `~/.claude/agent-telemetry`), `scripts/lease.py` + `test_lease.py` (shared-tool leases,
now with a `ps` fallback off Windows), `scripts/check_test_logs.py` (`"test_logs"` config), `templates/reviews/`
(daily code-review scheduled-task prompt + reviews README). habits-setup offers them (3c), habits-update refreshes
the copies. Checked against the originals on that project: `analysis.py` gives an identical `agent_runs.csv` (126
runs), `check_test_logs.py` the same 345/345; `test_lease.py` 9/9; `test_habits.py` 5 new checks, all pass.

## 2026-11-02 — task priorities and a queue in the plan (v0.8.2)
Source: one project replaced its plan lanes with a single priority queue (one task at a time) and patched its copy of
`tasks.py`; a habits-update refresh would have overwritten that. Ported: optional `**Priority:** P0-P3` header (once
one open task has it, all need it; index column), the plan's `Order:` paragraph as an alternative to lanes (board
sorts by Priority, then Order, then number; a closed or unknown id in it fails `--check`), and `--next` (what's next
without a model). Scheduler: writes Priority when the board uses it, adds to / removes from `Order:`, Next mode starts
from `--next`. `test_habits.py` covers all three.

## 2026-11-02 — scheduler frontmatter parses again (v0.7.2)
Source: subagent telemetry. 27 of 32 scheduler runs since 2026-11-01 ran on Opus although the agent says
`model: sonnet`: its description held `"trackers": task files`, a `": "` that makes the YAML invalid, so Claude Code
dropped the whole frontmatter (model, tools; the agent list showed "Agent from agent-habits plugin", all tools).
Routine Close / Queue calls cost ~$0.55 each on Opus vs $0.18 for the one Sonnet run. Fix: `-` instead of `:`;
`test_habits.py` now fails on a `": "` in any agent's or skill's plain frontmatter value (checked: fails on the old
file, passes on the new).

## 2026-11-01 — admin part review fixes (v0.7.1)
Source: review of v0.7.0. Fixes: pre-commit checks run with no stdin (one reading stdin swallowed the rest of the
list), cherry-pick / revert conclusions pass the guard like merges, `precommit` may be a string, python found as
`python3` or `python` with "no python" told apart from "habits.json invalid"; the shared-index guard
(`shared_index_guard`) and commit-msg hook are opt-in; setup asks before replacing another hook setup, makes the
hooks executable and notes `core.hooksPath` is per clone; default install trimmed (task files, tasks.py,
pre-commit `--check`, scheduler Queue / Close / Next; lanes, Inbox, review queue, guard, commit-msg, audit offered);
`tasks.script` dropped and `trackers.map` / `paths` derived from `tasks`; tasks.py: Status case-insensitive and
split on `(`, bad value named, `--project` without a value exits 2, Written only from the State when written
heading, undated closed tasks last, full lane labels, done list named by `tasks.index`; scheduler lane rules from
the plan's intro, no 14-day "last updated" audit check, done-task Status ignored; habits-update compares copies with
`grep -v`; `.gitattributes` keeps the hook templates LF. Tested: `test_habits.py` all pass, new cases for the
derived trackers, tasks.py fixes and the hooks in a temp git repo (guard off by default, whole-index blocked with
it on, pathspec passes, a stdin-reading check doesn't swallow the next, string precommit, invalid JSON, cherry-pick
conflict commit passes, bad subject blocked); the old hook fails the stdin and cherry-pick cases.

## 2026-11-01 — admin part: tasks, generated board, hook templates (v0.7.0)
Source: one project's process audit. Its task tracker (task files with Status / Needs you headers, a generated
index / plan board / done list, a shared-index pre-commit guard, a commit-message hook) worked, but lived in that
project, while the plugin's scheduler agent still wrote hand-kept index rows, a "needs first" column and
strike-through in the plan, and the plugin's examples, test fixtures and binary-extension list named that project's
formats. The user wants one practice for any project they or others delegate work from, with outside tracker
links. Changes: `scripts/tasks.py` (config `tasks` in habits.json: dir, prefix, plan, index, done; plan and lanes
optional; `**Links:**` shown in the index; `--check`), `templates/` (git hooks: shared-index guard + `precommit`
commands, commit-msg; starter task README / PLAN / done list, Inbox, review queue), the scheduler rewritten to that
format (Queue writes a task file + lane + runs tasks.py; Close appends Resolution, `git mv`, out of the lane, runs
tasks.py; no index rows), `habits-setup` scaffolds it on request, `habits-update` refreshes copies whose version line
is older, README "Admin" section, habits wording ("task", one place for each thing). Project leaks removed: binary
extensions (now `guards.binary_ext`), test fixtures, experiment-plan and example config. Tested: `test_habits.py`
(all pass, new: `binary_ext`, tasks.py on a template project with an open, a blocked and a closed task, a lane, no
plan, a broken header); the source project's 20 open / 24 closed tasks give the same blocks as its own generator
(except the example id in one prompt line); the hooks in a temp repo (whole-index commit blocked, pathspec passes,
broken header / stale board block, bad subject blocked).

## 2026-11-01 — setup check in experiment-plan (v0.6.1)
Source: one project's first tracker audit counted 6 reruns caused by the trial's setup rather than the thing under
test (a trial that started lifted off the floor, one cut short by a wall, a stepping run that never advanced).
Change: `skills/experiment-plan` section 4 gets a "setup check": each trial prints its starting state (position,
wall / ledge distance, facing, velocity, level loaded), the plan states the expected values, and they are compared
before any result is read. Text only; `test_habits.py` unchanged.

## 2026-11-01 — scheduler agent and tracker guard (v0.6.0)
Source: the user's question whether ticket closes update the plan. In one project the last four closes all moved the
ticket and its index row, but one (of four) left the ticket open in the plan: the closing steps never named the
plan, so it was updated by habit. Most queued work there is found mid-task by the session doing other work, which
then writes the ticket itself. Changes: `agents/scheduler.md` (Queue / Close / Next / Audit, from the project's
map, like the librarian), `skills/scheduler-audit`, a `trackers` config whose `paths` only the scheduler writes
(same hook as `knowledge_paths`; ticket files stay open for progress notes), a habits line, Hollis in the Estate
style. Tested: `test_habits.py` (main-session plan edit blocked with a pointer to the scheduler, scheduler passes,
librarian blocked on the plan and scheduler on knowledge, ticket file edit passes, shell append blocked).

## 2026-11-01 — per-agent use stats (v0.5.3)
Source: the user asked whether the agents help, as step 1 of a retrospective that would later look for new agent
opportunities and agents to retire. `kb_audit.py` counted only the librarian; `agent_stats.py` covers every agent
type. First run, brawl-coding transcripts 2026-10-14 to 11-01: 83 runs (79 background), matching the meta.json count
per type; librarian tokens 14.9M, equal to `kb_audit --use`. Re-reads within 15 calls: 3 of the 30 runs that used
Read (pplus-lookup 1, general-purpose 2); 53 runs read only via Grep / Bash, which the check doesn't see yet.
Background hand-backs come through a `SubagentHandback` tool call; the first version missed them (answer size 0).

## 2026-11-01 — optional Estate output style (v0.5.2)
Source: the user's request. The Estate style lived only in the user's `~/.claude/output-styles/`; it moves into the
plugin's `output-styles/` so every machine gets it, off unless picked. The plugin copy names only the plugin's and
Claude Code's built-in agents (project agents get a made-up role), per this repo's no-project-content rule.

## 2026-11-01 — context warning: user prompts only, hand off at phase boundaries (v0.5.1)
Source: the user's review of one session (brawl-coding / exporter H-26 + H-20). The 200K warning fired on a Dolphin
trace's task-notification, not a user prompt, and led to a mid-step handoff that cost ~15 tool calls (committing a
half-done state, progress notes, a session card) and a crossed commit in the shared git index; the session card was
unusable on mobile and the user finds session cards hard to organize. Changes: the warning skips background prompts
(task notifications, other sessions' and agents' messages, side-session and CI events) and waits for the next
prompt the user types; its advice now says to hand off at a phase boundary (step finished, verified, committed) as a
written brief the user starts, or `/compact <focus>` (the only option on mobile).

## 2026-11-01 — inline-write guard, librarian index and use stats (v0.5.0)
Source: a measurement of the librarian's first 1.5 days from the transcripts (18 runs, 9 sessions; 15 File, 3 Find).
Find answers were good (entries, status, evidence; caught an entry title overstating its test) but cost 0.6-1.7M
cache-read tokens each from wide greps, and nothing recorded whether they saved research. 14 of 18 runs edited the
docs with `python - <<EOF` read-modify-write scripts or `sed -i`, which the shell-write guard let through on
purpose ("a heredoc that only feeds a program"). Replaying 12,736 Bash commands from two projects' transcripts
through the new check: 1,325 such inline edits (1,305 in main sessions), 15 of 15 sampled hits real; the false
positives found on the way (scratchpad via `$S` or `cd`, git-ignored `out/`) are exempt and tested. Changes: the
inline-write guard; librarian edits with the Edit tool only and refreshes an entry index that Find greps first;
Find ends with `Result: hit / partial / miss`; `kb_audit.py` counts librarian runs, tokens and Find results from
the transcripts and suggests renames for stale "Used in" names, which Audit may fix once confirmed (the 2026-10-31
audit had 38 stale names and fixed none).

## 2026-10-31 — shell / file guards, experiment gate (v0.3.0)
Source: a `/insights` report over 190 sessions (buggy code and wrong approach as top friction; heredoc / NUL /
form-feed / CRLF file damage, runs launched on unverified assumptions or missing files). The prose rules existed in
memory and didn't stop the repeats, so they became hooks: built-in `shell_writes` block (heredoc / multi-line echo /
Set-Content into non-temp files) and `control_chars` report (control bytes, invalid UTF-8, mixed line endings after
an edit), both on without config, off via `"guards"`. New `experiments` config + `experiment-plan` skill: the first
long-run launch in a session with no plan file edited gets one reminder (PreToolUse `additionalContext`; a
reminder, not a block, per the hooks-only-for-zero-exception-rules guidance). `test_habits.py` covers all of it.

## 2026-10-14 — initial version
Habits from the author's own project sessions: evidence-first, expected results before work, 3-attempt cap, advisor
suggestions only at hard points (`/advisor`, Claude Code docs "advisor"), rules → hooks, cheap read-only agents,
background long runs, reminder hooks for changelog / knowledge notes.
