---
name: habits-update
description: Check what's new in Claude Code (changelog, docs, Anthropic engineering posts) and propose changes to the agent-habits plugin - new habits, hooks, agents, or habits made obsolete by new features. Use when the user asks "what's new", "any new Claude Code advice", "update my habits", or on a scheduled run.
---

# Update agent-habits from the latest Claude Code changes

Work in a clone of the `agent-habits` repo (github.com/CVaranese/agent-habits; on the home PC
`D:\git_repos\agent-habits`). Never change the plugin itself in this procedure: it only proposes.

## 0. Load the state
`git fetch origin habits-state && git show origin/habits-state:state.json`: `last_checked`,
`last_seen_changelog_version`, `docs_pages` (the docs index URLs seen last time).

## 1. Read everything new (all three, every time; no skimming)
1. **Changelog.** Download it with `curl -sL https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md`
   (not WebFetch: it truncates). Read every `## <version>` section newer than `last_seen_changelog_version`, in
   full. Note the newest version.
2. **Docs.** Download `https://code.claude.com/docs/llms.txt` with curl, extract the page URLs, diff against
   `docs_pages`. Open every new page in full. Also open the pages whose index description changed, and
   these pages every run: hooks, settings, sub-agents, skills, plugins, memory, costs (they change most often).
3. **Anthropic posts.** WebSearch for Claude Code posts since `last_checked` on `anthropic.com/engineering`,
   `anthropic.com/news` and `claude.com/blog` (e.g. `Claude Code site:anthropic.com/engineering`, and the same for
   the other two). Open every post published after `last_checked` that is about how to work with Claude Code.
   Anything the user pasted (a screenshot, a post): a suggestion to test, not a fact.

Keep a list of what you read (versions, pages, posts): it goes into the report.

## 2. Filter hard
Keep only what changes how the user works:
- a new feature that replaces one of our habits or hooks: propose removing ours;
- a feature that makes a habit enforceable or cheaper (hooks, agents, settings, commands);
- a practice that can be enforced or checked.
Skip ceremony, always-on token costs, and features for platforms the user doesn't use. Check every candidate
against `plugins/agent-habits/habits.md`, `hooks/hooks.json`, `scripts/habits.py`, `agents/`, `skills/`: no
duplicates. "Worth watching" items (not proposals yet) are allowed, one line each.

## 3. Save the state (always)
Write the new `state.json` (`last_checked` = today, newest changelog version read, the current `docs_pages` list)
and commit it to the `habits-state` branch (not `main`): check it out from `origin/habits-state`, commit, push.

## 4. Report
- **At least one proposal:** branch `habits-update/<YYYY-MM-DD>` from `main`; add a dated section to `UPDATES.md`
  with each proposal (what it is + source link, what to change in which file, one-line why, cost) and the watch
  items; commit, push, open a PR against `main` titled `Habits update <YYYY-MM-DD>`, body = the proposals.
- **No proposals:** no branch, no PR, no change to `main`. Send a short push notification instead: "Habits check
  <date>: nothing to change", the number of versions / pages / posts read, and any watch items.
- Interactive run (the user asked): show the list in the chat instead and apply only what they approve (edit the
  plugin, bump `version` in `.claude-plugin/plugin.json`, commit, push).
- Commits and PRs: no Co-Authored-By trailer, no "Generated with Claude Code" line.
