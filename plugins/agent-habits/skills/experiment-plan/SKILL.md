---
name: experiment-plan
description: Write the plan for a long scripted experiment (emulator / game-engine / build runs, anything over about 3 runs or a minute each) before launching it - goal, assumptions tagged verified / needs-user / unknown, prerequisites checked on disk, the cheapest run that could disprove the main assumption, run budget. Use before the first such launch in a session, when the agent-habits long-run reminder fires, or when the user asks to "plan the test first".
---

# Experiment plan (before the first long run)

Long scripted runs have been launched on wrong assumptions (a documented input rate that was wrong, a savestate
that didn't exist, menus navigated by trial and error for half a session). A plan costs one short file; a wasted
run costs minutes and a lot of context. Write it, then stop for the user when anything is tagged `needs-user`.

## 1. Goal
One sentence: the number or yes/no the runs must produce, and what decision it feeds.

## 2. Assumptions, each tagged
Every fact the run design rests on (timing, polling rates, which file / state the tool loads, what the output
contains, how long a run takes). Tag each one:
- `verified` - with the source: a knowledge note (`docs/knowledge/`), a test log, a memory entry, a measured run.
  Check the note exists and says that; a remembered "it's documented" is not verified.
- `needs-user` - only the user knows or can cheaply do it (a savestate, a menu step, a recording, "does P+ poll
  every frame?"). Ask these before the first run, in one message.
- `unknown` - nobody knows yet; the cheapest run below should settle it first.

## 3. Prerequisites on disk
List every file the run needs (specs, savestates, builds, scripts, output folders) with a check that it exists
(`ls`, `Test-Path`, a size). No run starts on a missing path.

## 4. Cheapest disproving run
The one shortest run that would show the main assumption wrong (one move, one frame window, one input). Run it
first; read its output before planning the rest.

## 5. Run budget
Number of runs and total minutes you will spend before reporting back, even without an answer. State the expected
result (numbers) for each planned run; a miss is a finding, not a reason to edit the expectation.

## Where it goes
The project's test-log or notes folder (the `experiments.plan` paths in `.claude/habits.json`), as the top of the
run's log file, dated. A plan written there this session silences the long-run reminder. Keep it short: ten lines
is normal.
