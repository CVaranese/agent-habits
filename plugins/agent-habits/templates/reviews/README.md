# Daily code reviews

A local scheduled task (`daily-code-review`, Claude desktop app) reviews the previous day's commits in <repos> and
writes what it finds here. It fixes nothing: the user picks what to fix, and the session that fixes a finding
updates its status. Open findings are handed to the work queue in the same run: one `Queue:` call to the
`agent-habits:scheduler` agent, which files them into an open task or one task for that review day, and a `Queued:`
line under Findings says where each went. A run that can't reach the scheduler writes a `## Queue request` section
instead (the exact `Queue:` text); the next session hands it over and deletes the section.

## Files
One file per reviewed day, `<YYYY-MM-DD>.md`, named after the day the commits were made (not the run day). A day
with no commits in any repo gets no file.

    # Review of 2026-10-31
    Run: 2026-11-01 08:04, /code-review medium (or: read-only subagent, correctness only)
    | Repo | Range | Commits |
    |---|---|---|
    | app | abc1234^..def5678 | 12 |
    | api | - | 0 |

    ## Findings
    1. **open** · app `src/session.py:120` · what is wrong, in one sentence.
       Failure: concrete inputs or state -> wrong output or crash. Verdict: confirmed / plausible.
    2. **fixed** in app 0a1b2c3 · ...
    3. **dismissed** (reason) · ...

    Queued: 1 -> T-<n>, 2 -> T-<n>, 3 -> idea (the scheduler's answer)

Status values: **open** (new), **fixed** in `<repo> <commit>` (that commit's body quotes the finding), **dismissed**
(with the reason: style only, not reachable, intended). Findings stay in the file once written; only their status
changes. "No findings" is a valid result and is written as such.

## Reading them
Open findings across days: `grep -rn "\*\*open\*\*" <reviews dir>/`.
