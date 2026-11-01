# Updates log

(Routine bookkeeping — last check, changelog version, docs pages seen — lives in state.json on the `habits-state` branch.)

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
