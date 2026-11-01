# agent-habits

Personal Claude Code habits as a plugin, so every machine and account gets the same working style. General rules
only: nothing from a specific project or employer goes in this repo.

If these habits are useful to you, a ⭐ on the repo helps others find them.

## What's in it (`plugins/agent-habits/`)
| Part | What it does |
|---|---|
| `habits.md` + SessionStart hook | Injects the working habits (~450 tokens) at the start of every session: evidence first, expected results before work, 3-attempt cap, advisor only at hard points, rules → hooks, token economy, no two editors on one file. |
| `scripts/habits.py` + `hooks/hooks.json` | Per-project rules from `<project>/.claude/habits.json`: **block** commands (pattern / unless), **check** files after edits (e.g. a syntax check), **reminders** at session end when work happened (edits, commands, reads, web) but the matching docs (changelog, knowledge notes) weren't updated. No config file → only the habits text and token hooks. |
| Token hooks (same script, no config needed) | **Context warning** on each prompt once the session's context passes 200K tokens, again every +100K: tells the user and asks Claude to offer a fresh session if the topic changed. **Big-output nudge** after a main-session tool output over ~5K tokens or an image read past 200K context. Override with `"tokens": {"context_warn", "context_step", "big_output"}` in `habits.json`. |
| Shell / file guards (same script, no config needed) | **Shell writes**: a Bash/PowerShell command that puts multi-line text into a file (heredoc + `>`/`>>`/`tee`, `echo`/`printf` with newlines, PowerShell here-string / `Set-Content` / `Out-File`) is blocked with a pointer to Write/Edit; heredocs that only feed a program (`git commit -F -`, `python -`) and writes into tmp / temp / scratchpad paths pass. **Inline writes**: inline Python (`python - <<EOF`, `python -c`) that opens a file for writing (`open(p, 'w')`, `.write_text`) and `sed -i` / `perl -i` on a `.md` file are blocked when the target is a source file: not temp (after the command's own `S=...` variables and `cd`) and not git-ignored; scripts run from a file (`python tools/x.py`) aren't looked into. **Control characters**: after every Edit/Write the file is scanned for NUL, form feed and other control bytes, invalid UTF-8 (cp1252 writes) and mixed CRLF/LF; a hit is reported back to Claude. Switch off per project with `"guards": {"shell_writes": false, "control_chars": false}`. |
| Experiment gate (`"experiments"` in `habits.json`) | The first launch of a long-run command (`on_command`) in a session with no edit to a plan file (`plan` path patterns) gets a one-time reminder to run the `experiment-plan` skill first. A reminder, not a block. |
| Knowledge guard (`"knowledge_paths"` in `habits.json`) | Edit / Write / shell writes (redirects, `tee`, `sed -i`, `mv`, `rm`, `cp` into, `Set-Content`...) to the knowledge base paths are blocked unless the call comes from the `librarian` agent (hook input `agent_type` = `agent-habits:librarian`); the message says how to hand findings over. Reminders with `"to_librarian": true` point at the librarian too. |
| `agents/librarian.md` (Sonnet) | The only writer of the knowledge base. **File**: a session's findings (fact, evidence, status) merged into the right entry from the project's map (`knowledge_map`), no duplicates, links both ways. Edits with the Edit tool only, then refreshes the entry index. **Find**: what's recorded about X, with sources (read-only; call it with model haiku): greps the entry index first, reads only the matching entries, ends with `Result: hit / partial / miss`. **Audit**: runs `scripts/kb_audit.py`, judges the rest, fixes certain mechanical findings (confirmed one-candidate renames included), writes the report. |
| Tracker guard (`"trackers"` in `habits.json`) | Same block as the knowledge guard for `trackers.paths` (the ticket index and plan): only the `scheduler` agent (`agent-habits:scheduler`) writes them. Ticket files themselves stay open, so a working session keeps its own Progress notes. |
| `agents/scheduler.md` (Sonnet) | Keeper of the work queue, from the project's map (`trackers.map`: ticket format, index, closing steps). **Queue**: work a session found mid-task → a ticket in the project's format (or an idea line, or a note on the existing ticket), no duplicates across the open, done and other lists (`trackers.lists`), index row, placed in the plan's wave / lane by shared files. **Close**: resolution, `git mv` to done, index / done / plan rows, unblocked tickets. **Next**: what to run next, read-only (model haiku). **Audit**: list health, cycle times, failed runs, rework; fixes the certain ones, writes `trackers.audit.report`. |
| `skills/scheduler-audit` | Scheduled / on-demand tracker audit: runs the scheduler in Audit mode, spot-checks its counts, commits the report. |
| `skills/librarian-audit` + `scripts/kb_audit.py` | Scheduled / on-demand audit (`"knowledge_audit"` in `habits.json`: `files`, `code_roots`, `repos`, `report`, optional `index`, `transcripts`): cited paths that moved or are gone, "Used in" names found in no code (with `renamed? X` guesses: a dropped part, or a camelCase / constant name turned table field), broken links / anchors, duplicate titles / anchors, commit hashes no repo resolves, memory `[[links]]` with no memory. Also the librarian's use from the transcripts (runs and tokens per mode, Find hit / partial / miss) and the entry index (one line per entry: `file#anchor L<line> [status] title \| used in`; `--index` rewrites only that, `--use` prints only the use). |
| `scripts/agent_stats.py` | Per-agent use from the transcripts (`--project`, `--transcripts`, `--all`, `--since`): runs (background / foreground), models, tokens per run (fresh vs cache reads), tool calls, minutes, answer size, and **re-reads**: runs whose Read files the main session Read again within `--window` (15) tool calls of the answer, a sign it redid the work or didn't trust it (Read only, so a floor). `--detail` lists those runs. |
| `skills/experiment-plan` | The plan written before a long scripted experiment: goal, assumptions tagged verified / needs-user / unknown, prerequisites checked on disk, the cheapest disproving run, run budget; stops for the user on anything `needs-user`. |
| `agents/source-lookup.md` (Sonnet) | One factual question → values with sources and kinds (data / derived / secondary / guess). Keeps big reads out of the main session. |
| `agents/log-triage.md` (Haiku) | Large logs → only errors, crashes and the requested lines. |
| `output-styles/estate.md` (optional) | "Estate" output style: subagent dispatches and returns told in one line as the staff of a small estate (Mabel the librarian, Pip, Hettie...); everything else, and anything that leaves the chat, unthemed. Off unless picked in the output-style setting. |
| `skills/habits-setup` | Sets up `.claude/habits.json` + a CLAUDE.md section in a new project. |
| `skills/habits-update` | Checks the Claude Code changelog / docs / posts since the last check and proposes changes in `UPDATES.md`. |

Requires `python` on PATH (3.8+). If Python is missing, the hooks fail and nothing is blocked.

## Install
From a clone or a local folder:

    claude plugin marketplace add <path-to-clone>/agent-habits
    claude plugin install agent-habits@agent-habits

From GitHub (once pushed; private repos use your git credentials):

    claude plugin marketplace add <user>/agent-habits
    claude plugin install agent-habits@agent-habits

Inside a session (desktop app too): `/plugin marketplace add ...`, then `/plugin install agent-habits@agent-habits`.
Then in each project: "set up habits" (runs `habits-setup`).

Work machines: managed settings can restrict marketplaces (`strictKnownMarketplaces`) or plugins. If the add or
install is refused, ask whoever manages Claude Code there; don't work around it.

## Update
- Edit here, bump `version` in `plugins/agent-habits/.claude-plugin/plugin.json`, push.
  Other machines: `claude plugin marketplace update agent-habits` (or `/plugin`), then restart the session.
- Ask Claude "update my habits" (skill `habits-update`) to check for new Claude Code features and advice.
- To hear about new versions, star the repo or use **Watch > All Activity** on GitHub (versions ship as pushes,
  not GitHub Releases).

## Test
    python -I plugins/agent-habits/scripts/test_habits.py
    claude plugin validate .
