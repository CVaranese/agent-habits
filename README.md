# agent-habits

Personal Claude Code habits as a plugin, so every machine and account gets the same working style. General rules
only: nothing from a specific project or employer goes in this repo.

## What's in it (`plugins/agent-habits/`)
| Part | What it does |
|---|---|
| `habits.md` + SessionStart hook | Injects the working habits (~450 tokens) at the start of every session: evidence first, expected results before work, 3-attempt cap, advisor only at hard points, rules → hooks, token economy, no two editors on one file. |
| `scripts/habits.py` + `hooks/hooks.json` | Per-project rules from `<project>/.claude/habits.json`: **block** commands (pattern / unless), **check** files after edits (e.g. a syntax check), **reminders** at session end when work happened (edits, commands, reads, web) but the matching docs (changelog, knowledge notes) weren't updated. No config file → only the habits text and token hooks. |
| Token hooks (same script, no config needed) | **Context warning** on each prompt once the session's context passes 200K tokens, again every +100K: tells the user and asks Claude to offer a fresh session if the topic changed. **Big-output nudge** after a main-session tool output over ~5K tokens or an image read past 200K context. Override with `"tokens": {"context_warn", "context_step", "big_output"}` in `habits.json`. |
| Shell / file guards (same script, no config needed) | **Shell writes**: a Bash/PowerShell command that puts multi-line text into a file (heredoc + `>`/`>>`/`tee`, `echo`/`printf` with newlines, PowerShell here-string / `Set-Content` / `Out-File`) is blocked with a pointer to Write/Edit; heredocs that only feed a program (`git commit -F -`, `python -`) and writes into tmp / temp / scratchpad paths pass. **Control characters**: after every Edit/Write the file is scanned for NUL, form feed and other control bytes, invalid UTF-8 (cp1252 writes) and mixed CRLF/LF; a hit is reported back to Claude. Switch off per project with `"guards": {"shell_writes": false, "control_chars": false}`. |
| Experiment gate (`"experiments"` in `habits.json`) | The first launch of a long-run command (`on_command`) in a session with no edit to a plan file (`plan` path patterns) gets a one-time reminder to run the `experiment-plan` skill first. A reminder, not a block. |
| `skills/experiment-plan` | The plan written before a long scripted experiment: goal, assumptions tagged verified / needs-user / unknown, prerequisites checked on disk, the cheapest disproving run, run budget; stops for the user on anything `needs-user`. |
| `agents/source-lookup.md` (Sonnet) | One factual question → values with sources and kinds (data / derived / secondary / guess). Keeps big reads out of the main session. |
| `agents/log-triage.md` (Haiku) | Large logs → only errors, crashes and the requested lines. |
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

## Test
    python -I plugins/agent-habits/scripts/test_habits.py
    claude plugin validate .
