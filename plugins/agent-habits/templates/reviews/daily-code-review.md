Daily code review of yesterday's commits in local git repos. Review only: fix nothing, edit no code.

Repos (absolute paths; the first one holds the reviews folder):
<repos: one line each, "- <name>: <absolute path>">

Reviews folder: <first repo>/<reviews dir> (format in its README.md).

1. Let D = yesterday's date (YYYY-MM-DD, local time). In each repo run
   `git -C <repo> log --reverse --format="%h %s" --since="<D> 00:00" --until="<today> 00:00" <branch>`.
   Ignore commits whose subject starts with `review:` (earlier runs of this task). If no repo has commits, stop
   here: write nothing, commit nothing, and end with "No commits on <D>".
   If <reviews folder>/<D>.md already exists, stop too ("already reviewed").
2. For each repo with commits, the range is `<first>^..<last>` (first and last commit of that day). Review that diff
   for correctness bugs only (skip style, naming, docs wording):
   - First choice: run `/code-review medium` on that range (the code-review skill) in that repo.
   - If that skill can't run here, review `git -C <repo> diff <first>^..<last>` with one read-only subagent
     (Explore or general-purpose) and the prompt "correctness only, skip style: list each bug as file:line, one-sentence
     defect, concrete failure scenario (inputs/state -> wrong output or crash), confirmed or plausible". Skip diffs of
     docs and *.md files; review code.
   Write down which of the two ways you used.
3. Write <reviews folder>/<D>.md in exactly the format that folder's README.md describes (read it first): title, run
   line (date/time and method), a table with each repo's range and commit count, then numbered findings, each with
   status **open**. If there were none, write "No findings." under Findings.
4. Commit only that file, staging and committing in one command (other sessions may share the index):
   `git -C <first repo> commit -m "review: record the previous day's findings" -m "<D>: <N findings, repos and ranges, method>" -- <reviews dir>/<D>.md`
   after `git add` of that one path in the same shell command. Don't push.
5. Hand the open findings to the work queue (skip if "No findings."), so none is left without a home:
   - First note `git -C <first repo> status --porcelain -- <tasks dir>` (clean or not).
   - One Agent call, `subagent_type: "agent-habits:scheduler"`, prompt starting with `Queue:`. One item per open
     finding: what (file:line, the one-sentence defect), why (the failure scenario and verdict), evidence
     (`<reviews dir>/<D>.md` finding n, the commit range), files it touches, and as the user's question "fix or
     dismiss? (review finding, not yet triaged)". Ask it to put findings that belong to an open task into that
     task and the rest into one task for this review day, and to list every file it changed.
   - Under the review file's Findings add a line `Queued: <the scheduler's answer per finding (task or idea)>`.
   - Commit the review file and the files the scheduler listed, staging and committing in one command with explicit
     paths, as in step 4 (subject `review: queue the previous day's findings`). If the tasks folder was not clean
     before the call, commit only the review file and say in the summary that the tracker edits are uncommitted.
   - If this run has no Agent tool or no `agent-habits:scheduler` agent: instead add a `## Queue request` section to
     the review file holding the exact `Queue:` prompt, commit it as in step 4, and say in the summary that the next
     session must hand it to the scheduler.
6. End with a short summary: number of findings per repo, the top one or two, and where they were queued (task ids,
   or "Queue request written"), so the user can pick what to fix.
