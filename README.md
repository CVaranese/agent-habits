# agent-habits

Personal Claude Code habits as a plugin, so every machine and account gets the same working style. General rules
only: nothing from a specific project or employer goes in this repo.

## What's in it (`plugins/agent-habits/`)
| Part | What it does |
|---|---|
| `habits.md` + SessionStart hook | Injects the working habits (~450 tokens) at the start of every session: evidence first, expected results before work, 3-attempt cap, advisor only at hard points, rules → hooks, token economy, no two editors on one file. |
| `scripts/habits.py` + `hooks/hooks.json` | Per-project rules from `<project>/.claude/habits.json`: **block** commands (pattern / unless), **check** files after edits (e.g. a syntax check), **reminders** at session end when work happened (edits, commands, reads, web) but the matching docs (changelog, knowledge notes) weren't updated. No config file → only the habits text. |
| `agents/source-lookup.md` (Sonnet) | One factual question → values with sources and kinds (data / derived / secondary / guess). Keeps big reads out of the main session. |
| `agents/log-triage.md` (Haiku) | Large logs → only errors, crashes and the requested lines. |
| `skills/habits-setup` | Sets up `.claude/habits.json` + a CLAUDE.md section in a new project. |
| `skills/habits-update` | Checks the Claude Code changelog / docs / posts since the last check and proposes changes in `UPDATES.md`. |

Requires `python` on PATH (3.8+). If Python is missing, the hooks fail and nothing is blocked.

## Install
From a clone or a local folder:

    claude plugin marketplace add D:/git_repos/agent-habits
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
