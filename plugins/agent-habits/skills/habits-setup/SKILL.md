---
name: habits-setup
description: Set up the agent-habits workflow in the current project - write .claude/habits.json (reminder hooks for changelog / knowledge notes, blocked commands, on-edit checks) and a short CLAUDE.md section. Use when the user starts using Claude Code in a new project, asks to "set up habits", or wants a reminder/check added to a project.
---

# Set up agent-habits in this project

Goal: a small, accurate config. Every rule must be one the project really needs; an empty section is fine.

## 1. Look before asking
Read the project's CLAUDE.md / README and list the top-level folders. Find what exists already: a changelog, a
notes / knowledge / decisions folder, a test or build command, a linter or type checker, risky commands (deploys,
publishes, migrations), log locations. Existing `.claude/habits.json`: read it and edit, don't replace.

## 2. Ask once (AskUserQuestion, only what you couldn't find)
- Changelog: does user-facing work need a changelog line? Which file, which paths count as user-facing changes?
- Knowledge: where should findings from research go (a docs folder)? Which sources count as research (sites,
  reference folders, test/probe commands)?
- Checks: a fast per-file check worth running after every edit (syntax check, formatter --check, type check of one
  file)? Must take seconds.
- Blocks: commands that must never run in a certain form (e.g. a deploy without a flag)?
- Review list: should things only the user can check go in a file (e.g. `docs/REVIEW_QUEUE.md`)?

## 3. Write `.claude/habits.json`
Schema and a full example: `${CLAUDE_PLUGIN_ROOT}/examples/habits.example.json`. Keys: `block` (tools, pattern,
unless, message), `check` (path regex, command argv with `{file}`, message), `reminders` (name, on_edit,
on_command, on_read, on_url, on_search, docs, message, optional once_per_session: remind at most once per
session, for rules that trigger on every turn of a research or test session), `experiments` (on_command = long-run
launches, plan = path patterns of the plan / test-log files; one reminder per session to run the `experiment-plan`
skill when a launch comes before a plan), `guards` (shell_writes, control_chars; both on by default, set false only
with a reason), `knowledge_paths` (path regexes only the `librarian` agent may write: knowledge notes, pitfalls,
the auto-memory `/\.claude/projects/<project>/memory/`), `knowledge_map` (the doc(s) saying where each kind of fact
goes and its entry shape), `knowledge_audit` (files, code_roots, repos, report: input of the `librarian-audit`
skill; offer a weekly scheduled run), `to_librarian: true` on the knowledge reminder, `trackers` (when the project
keeps a queue of tickets / handoffs: map, paths only the `scheduler` agent writes (index and plan, not the ticket
files), plan, done, lists, audit.report for the `scheduler-audit` skill; offer a weekly scheduled run). Patterns are case-insensitive
regexes; paths use forward slashes. Keep triggers narrow: a reminder
that fires on unrelated work trains everyone to ignore it.

Test each rule before finishing: pipe a sample hook JSON into
`python -I "${CLAUDE_PLUGIN_ROOT}/scripts/habits.py" <pre-tool|post-tool|stop>` with `cwd` set to the project.
Write sample inputs to a temp file first, so the test command itself doesn't trip the live hooks.

## 4. CLAUDE.md
Add a short section (create the file if missing): where the changelog, knowledge folder and review list live, the
test/build commands, and "rules enforced by `.claude/habits.json`" with one line per rule. Don't copy the general
habits; the plugin injects those every session.

## 5. Report
What was configured, what was tested (with results), what the user declined or still needs to decide.
