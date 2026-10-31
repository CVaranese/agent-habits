---
name: librarian-audit
description: Audit the project's knowledge base with the librarian agent - stale paths and code names, broken links, duplicate entries, unknown commits, memory that contradicts the docs - fix the mechanical ones and write the report (habits.json knowledge_audit.report). Use on the weekly scheduled run, when the user asks to "audit the knowledge base" / "check the docs are still right", or after a big refactor that moved files.
---

# Knowledge-base audit

1. Read `.claude/habits.json` in the project. No `knowledge_audit` section: say so and stop (the `habits-setup`
   skill adds one: `files`, `code_roots`, `repos`, `report`).
2. Start the librarian (Agent tool, `subagent_type: agent-habits:librarian`, default model) with:

       Audit: project <absolute project dir>. Mechanical checks: python -I "${CLAUDE_PLUGIN_ROOT}/scripts/kb_audit.py" --project <absolute project dir>.
       Fix only certain mechanical findings, write the report to knowledge_audit.report, report the counts and
       every file you changed.

3. When it returns: read the report's summary lines (not the whole file) and check the files it says it changed
   with `git diff --stat` in each repo. Anything changed outside the knowledge paths is a bug in the run: report it.
4. Tell the user: counts per check, what was fixed, the items waiting for them (link the report). Commit the report
   and the fixes only if the user (or the scheduled task's prompt) asked for commits, one commit per repo,
   subject like `docs: knowledge-base audit`.

Unattended (scheduled) runs: same steps; end with the summary as the task's result instead of asking anything.
