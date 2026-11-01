---
name: scheduler-audit
description: Audit the project's work trackers with the scheduler agent - stale lists, items in two lists, closed tickets still open in the plan, ticket cycle times, failed runs and rework, blocked tickets waiting on the user - fix the mechanical ones and write the report (habits.json trackers.audit.report). Use on a weekly scheduled run, or when the user asks to "audit the trackers" / "how is the queue doing".
---

# Audit the work trackers

1. Read `.claude/habits.json`: `trackers` must exist with `map` and `audit.report`. Missing: say so and offer to add
   it (skill `habits-setup`); stop.
2. Agent tool, `subagent_type: "agent-habits:scheduler"`, prompt starting with `Audit:` plus the project dir and,
   when the project keeps run logs or a tests index the map names, where they are. Run it in the background when
   other work is going on.
3. When it returns: read the report's findings (not the whole file), check one or two of its counts against the
   source it names, and commit the report and its list fixes with explicit paths (`git commit -- <paths>`).
4. Answer the user with the 3-5 findings and their proposed fixes. Proposals that change skills, hooks or CLAUDE.md
   wait for the user's yes.
