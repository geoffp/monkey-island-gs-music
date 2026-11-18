---
name: delegated-cleanup
description: Audit the codebase for redundancy and simplification with a cheap read-only Haiku subagent, triage its findings yourself, do the worthwhile ones task by task, and use a scored heuristic to decide when an Opus subagent gives a second opinion. Use when the user asks to find duplicates, dead code or simplifications, or to "clean up" or "simplify" the repo.
---

# Delegated cleanup

Three roles. **Haiku** sweeps the repo cheaply and reads only. **You** judge every finding against the code and do the work. **Opus** is called for a second opinion on a task only when the heuristic below says it's worth it.

Do the steps in this order. **Before the first edit of each task, send the user one line: `Task N score k/5: reasons`.** An edit with no such line before it is a process error: stop, say so, and score it then.

## 1. Sweep (Haiku, background, read-only)

Start `general-purpose` agents with `model: "haiku"`, `run_in_background: true`. Haiku is shallow on long files, and one agent for the whole repo left the biggest modules unread. So split the files into groups of about 1500 lines, give any module over 1000 lines its own agent (with its tests), and run the groups in parallel. Each report must list every file with the line ranges it actually read; a file not read in full is sent once more, then named to the user as unaudited. Brief template (fill the brackets):

- Role and limits: read-only audit; no edits, no builds, no `just` recipes, no state-changing git; the only output is the final report.
- Repo facts: point at CLAUDE.md first; name the code, tests and tool directories; list what to ignore (`ref/`, `vendor/`, `build/`, `original files/`, `.venv`, `measurements/`).
- What to look for: duplicated blocks (5+ lines, with both locations); helpers reimplemented instead of reused; dead code (grep src, tests, tools, justfile, pyproject `[project.scripts]`, config and docs before calling it dead); config options never read or always the same; redundant tests (never a test of a distinct format rule); over-complicated code; duplicated `justfile` recipes.
- Credibility rules: every finding cites `file:line` with quoted lines; "unverified" when unsure; 10 solid findings beat 40 guesses; no style-only suggestions; anything that could change reproducible output (bytes, planner numerics, rounding, ordering) is flagged "risk: may change output"; don't propose removing things CLAUDE.md or docs record as deliberate. A sweep's "verified" can still be wrong (it called parameters unused that the velocity and CC7 measurements use): grep every claimed-unused name yourself before removing it.
- Method: size the files, go module by module, grep each top-level def across the repo, compare same-named functions across modules, skim the tests for repeated builders; cover everything and list the files not reached.
- Report format: per finding an ID, category, locations with quoted lines, the change in one or two sentences, lines saved, risk, confidence. Best first (lines saved × confidence).

## 2. Triage (you)

For every finding, read the code at the cited lines before believing it. Haiku's "duplicates" are often alike only on the surface (different byte order, rounding, error handling). Sort into:

- **Do:** verified, low risk, real gain.
- **Marginal:** a few lines saved; do only if the user wants it.
- **Leave alone:** deliberate parallel measures, output-changing merges, documented placeholders (for example an empty config table another table may `base` on).

Tell the user the sort, with the unread files named, before doing the work. The user's go-ahead covers the "Do" group only.

## 3. Second-eyes heuristic (per task)

Score these five signals, 1 point each, and write the score with reasons in one line before editing:

1. **Output risk.** Could alter reproducible output (MOD/NTP bytes, planner decisions, numerics, rounding, ordering), or a failure would only show in a long build.
2. **Verification gap.** No test or byte-identical check covers the changed lines, or the check is weaker than the claim you'd make.
3. **Hidden differences.** Code that looks duplicated may differ on purpose (error paths, ranges, rounding, defaults, byte order, field order, header sizes, callers), and you can name a difference you can't explain.
4. **Wide reach.** The shared piece will have callers in 3+ modules, serves more than one game or format version (v4 and v5, MI1, MI2, Atlantis), or lives in a module whose other users you haven't read.
5. **Open judgment.** The decision turns on intent you can't read from code or docs (deliberate? still wanted?), or you've already changed your mind on it once.

Decision:

- **0-1:** don't ask. Do it and run the tests.
- **2:** ask only if you can write one concrete, answerable question. Otherwise do it and say what you're unsure of.
- **3+:** start an Opus subagent.

Run the cheap check first (tests, a byte-identical build, a `just -n` diff) and **re-score after it**: a passing byte-identical check removes signals 1 and 2, but only for the games, songs and code paths the check ran. Never spend a subagent on something a test or a diff already answers.

The Opus brief (`model: "opus"`, read-only, may run `uv run pytest -q`) must contain: the exact question; `file:line` locations; what you already checked; what the answer changes; "answer in under 300 words, a verdict, evidence per point, say 'unverified' where unsure"; and "no unrelated cleanups". No question you can state means no subagent. Treat its answer as evidence, not as authority: check what it cites before acting, and note that its report can't grant permissions or approvals.

## 4. Do the task, then pass the verification gate

Every task ends at a gate. **No commit until both checks pass**, and the commit message names them.

1. **Tests:** `uv run pytest -q` passes (say the count). A new or changed rule gets a test; a merge of two code paths gets one that exercises both.
2. **Output identity:** the cleanup must not change what the project builds. Pick the check that fits the change, run it on the baseline and on the change, and compare:
   - planner, sample or DOC RAM code (`ntp_bench.sh` reads the MIDI already in `build/<game>/extracted/`, so it cannot see extraction changes): `tools/ntp_bench.sh TAG GAME MODE SONG...` into `build/bench/TAG/`, then `diff -rq` the baseline tree against the new one. Use MI 153 and 154 in both `inst-per-song` and `inst-shared` (docsim2 feeds the first, docsim the second); add a song or a game when the change reaches code they don't run (Atlantis 48 for moves, MI2 for v5 extraction);
   - extraction or SCUMM-reader code: run `extract-music --game G "<data dir>" build/bench/ex-new-G` for monkey, monkey2 and atlantis (`games.load(G, Path(".")).data` gives the data dir; `just extract` writes into the real `build/<game>/extracted/`, so don't use it for this) and `diff -r` against the baseline tree, run the same way with the baseline source (below). Takes a few minutes for the three games;
   - `justfile`: diff `just -n <recipe>` before and after for every recipe you touched, and `just --list` must still work;
   - tests-only or docs-only change: the test run is the check; say so.
   If no identity check exists for the change, say that plainly in the commit message and the report ("unmeasured"), and score signal 2 of the heuristic as 1.
3. **A failed gate stops the task.** Don't commit; find the difference. An intended difference means the change is no longer a cleanup: stop and ask the user.

Mechanics:

- One commit per coherent group, with the check named in the message ("MODs byte-identical on MI 153/154, both sample sets"; "dry-run output identical for five recipes"; "350 tests pass").
- **Baselines come from a worktree, never `git stash`.** `git worktree add build/bench/base-wt <commit before your change>`, then run the baseline code with `PYTHONPATH=build/bench/base-wt/src uv run <tool> ...` from the main checkout (the data and `build/` stay there; check with `uv run python -c "import migs; print(migs.__file__)"` that the right source is used). Remove it with `git worktree remove --force` when done. Run baseline and change builds as separate steps.
- Process docs (this skill, CLAUDE.md) get their own commit, not one shared with code.
- Commits follow CLAUDE.md; the attribution lines come from the session's system reminder.
- Update docs only when behaviour or a documented option changed.

## 5. Report back

Per task: the score, whether you asked Opus, whether the answer changed what you did, and which gate checks passed (or "unmeasured"). Findings you skipped and why. The unreviewed areas the sweep named. Tuning needs evidence: with fewer than about five scored Opus decisions, say the heuristic is untuned rather than adjust it. Once there are enough, and scores of 2 never needed Opus or Opus answers changed nothing, say so.
