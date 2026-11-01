---
name: habits-setup
description: Set up the agent-habits workflow in the current project - write .claude/habits.json (reminder hooks for changelog / knowledge notes, blocked commands, on-edit checks), optionally the admin part (task files + generated board, scheduler agent, git hooks), and a short CLAUDE.md section. Use when the user starts using Claude Code in a new project, asks to "set up habits", or wants a reminder/check added to a project.
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
- Admin: do they want a task tracker to delegate work to agents (section 3b)?

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
skill; offer a weekly scheduled run), `to_librarian: true` on the knowledge reminder, `tasks` / `trackers` /
`precommit` (the admin part, section 3b). Patterns are case-insensitive regexes; paths use forward slashes. Keep
triggers narrow: a reminder that fires on unrelated work trains everyone to ignore it.

## 3b. Admin (task tracker), only when the user wants it
A task tracker the user can delegate from: one file per task (`**Status:**` / `**Needs you:**` / `**Links:**`), a
generated board, the `scheduler` agent as its only writer of the index and plan, git hooks. Ask (one
AskUserQuestion) which optional parts they want: plan lanes (parallel work; else no PLAN.md), a review queue, a
changelog reminder; the id prefix (default `T-`) and the folder (default `docs/tasks`). Then, with
`P=${CLAUDE_PLUGIN_ROOT}` and `V` = the `version` in `$P/.claude-plugin/plugin.json`:
1. Copy `$P/scripts/tasks.py` to `tools/tasks.py`, `$P/templates/githooks/pre-commit` and `commit-msg` to
   `.githooks/`, `$P/templates/tasks/` to the task folder (skip `PLAN.md` without lanes), `$P/templates/INBOX.md`
   (and `REVIEW_QUEUE.md` if wanted) next to the task folder. Existing files: never overwrite; show the difference.
   Plain copies (`cp`), then the Edit tool: in each copied script / hook replace `<version>` on its first lines
   with `V`; with another prefix, replace `T-` in the copied docs. `git add --chmod=+x .githooks/*`.
2. `.claude/habits.json`: `"tasks": {"dir", "prefix"}` (only what differs from the defaults `docs/tasks`, `T-`,
   `PLAN.md`, `README.md`, `done`), `"precommit": ["python -I tools/tasks.py --check"]` plus any fast project check
   the user names, and `"trackers"`: `map` = the task README, `paths` = regexes for the task README, PLAN.md and
   done/README.md (not the task files: working sessions write their own Progress), `lists` = the Inbox, review
   queue and other lists items may live in, `audit`: `report` (e.g. `docs/tasks/AUDIT.md`), optional `repos`
   (repos whose commits tasks cite) and `runs` (test-run indexes). Offer a weekly `scheduler-audit` run.
3. `git config core.hooksPath .githooks`, then `python -I tools/tasks.py` and `python -I tools/tasks.py --check`
   (must pass), and a test commit message through `.githooks/commit-msg` (a bad and a good subject).
4. CLAUDE.md: one paragraph: where tasks live, `Start T-<n>` to work one, new work goes through the scheduler
   (`Queue:` / `Close:` / `Next:`), commit with explicit paths (`git commit -- <paths>`).

Test each rule before finishing: pipe a sample hook JSON into
`python -I "${CLAUDE_PLUGIN_ROOT}/scripts/habits.py" <pre-tool|post-tool|stop>` with `cwd` set to the project.
Write sample inputs to a temp file first, so the test command itself doesn't trip the live hooks.

## 4. CLAUDE.md
Add a short section (create the file if missing): where the changelog, knowledge folder and review list live, the
test/build commands, and "rules enforced by `.claude/habits.json`" with one line per rule. Don't copy the general
habits; the plugin injects those every session.

## 5. Report
What was configured, what was tested (with results), what the user declined or still needs to decide.
