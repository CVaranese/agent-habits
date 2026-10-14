---
name: habits-update
description: Check what's new in Claude Code (changelog, docs, Anthropic engineering posts) and propose changes to the agent-habits plugin - new habits, hooks, agents, or habits made obsolete by new features. Use when the user asks "what's new", "any new Claude Code advice", "update my habits", or on a scheduled run.
---

# Update agent-habits from the latest Claude Code changes

The plugin repo is the source of truth: `${CLAUDE_PLUGIN_ROOT}` is the installed copy; edit the user's clone of the
`agent-habits` repo (ask where it is if unknown; on the home PC `D:\git_repos\agent-habits`).

## 1. Find what changed
- Read `last_checked` in the repo's `UPDATES.md` (create it if missing; default: 30 days ago).
- Sources, newest first, only entries after `last_checked`:
  - Claude Code changelog: `https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md` (raw:
    `https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md`)
  - Docs index: `https://code.claude.com/docs/llms.txt` (new or renamed pages; read only pages that look relevant)
  - Anthropic engineering / Claude blog posts about Claude Code workflows
  - Anything the user pasted (a screenshot, a post): treat as a suggestion to test, not as fact.

## 2. Filter hard
Keep only what changes how the user works:
- a new feature that replaces a habit or hook (e.g. a built-in setting that does what a hook does): propose
  removing ours;
- a feature that makes a habit cheaper or enforceable (hooks, agents, settings, commands);
- a practice that can be enforced or checked. Skip ceremony (mandatory plan files, always-on extras that cost tokens
  on every turn) and anything only usable on a platform the user doesn't have.
Check each candidate against `habits.md`, the hooks and agents: no duplicates.

## 3. Propose, don't apply
Write the proposals to `UPDATES.md` under today's date: per item, what it is (source link), what to change in the
plugin (file + change), why it's worth it in one line, and its cost (tokens per session, setup). Update
`last_checked`. Then show the user the list and apply only what they approve (bump `version` in `plugin.json`,
commit). On a scheduled / unattended run: write the proposals on a branch and open a PR instead of committing to
main.
