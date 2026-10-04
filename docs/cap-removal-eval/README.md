# #43 cap removal at matched tokens

Status: **running** (fresh-task generation). Production is unchanged.

- The protocol (`PROTOCOL.md`, sha256 `b8788652…`), the generation, run and score scripts, and
  `prereg.sha256` were frozen at `b400c13`, before any fresh task existed.
- This is an independent allocator experiment: it is not TreeSelect, and it does not rerank the
  shortlist. Its result is kept as input for the future index-time tree architecture
  (TreeIndex/TreeRouter, `docs/treeselect-eval/pageindex-notes.md`).

## Interruption and resume (2026-10-04), before any task was scored

- Generation was stopped once by decision, during the held-out stage. At that point the raw
  candidates for httpx, requests, flask and ripgrep were complete (`.done`), and clap was mid-run.
- The decision was reversed, and generation resumed with the same frozen script and commit
  (`gen_all.sh b400c13`).
- `make_tasks.py` reuses an existing worktree without re-indexing it. So the two clap worktrees created
  during the interrupted run (`clap-81dea3b6`, `clap-f5ba9c51`) were removed before resuming, and the
  clap stage regenerated from an empty `raw-clap.jsonl`, as an uninterrupted run would.
- Completed stages were left untouched. No ContextBench draw, arm run or score existed at the
  interruption.

## Phase split (2026-10-04, before any task was scored)

Time-boxed by decision. The frozen protocol, arms, metrics and gates are unchanged, and the
**verdict is still computed only on the full fresh set.**

- **Phase 1, pilot (descriptive only).** The held-out tasks of httpx, requests and flask run through
  the frozen pipeline: parent indexes, filters, edit-locus gold, eligibility, first 10 eligible per
  repo, parity, A/B, scoring. This is an end-to-end harness check on fresh data. The pilot tasks
  are the same tasks the full run will produce for those repos: the per-repo filters only look at
  earlier tasks of the same repo. They stay in the final verdict. Nothing changes after the pilot
  except a fix for a harness defect, which would be disclosed.
- **Phases 2–3.** The remaining held-out repos (zod, axios), then the ContextBench draw, then the full
  scored run, chained unattended.
- **Second pause.** Generation was paused again during zod (0 tasks emitted, no `.done` marker). All
  zod worktrees were removed so that stage regenerates as in an uninterrupted run.

## Phase 1 pilot result (descriptive only, NOT the verdict)

Scripts: `phases/`. Results: `results/phase1-pilot/` (`pilot.txt`, `packs.jsonl`, `parity_p1.tsv`,
`heldout-draws.jsonl`).

- Pipeline check, held-out httpx, requests and flask: 59 tasks at parent, 57 primary, 40 drawn,
  **30 kept** (10 per repo).
- P1: parity 30/30, B repeats 30/30.
- B vs A: coverage 0.5315 → 0.5993, Δ +0.0677, 3 up / 0 down. rel/used +5.0 %; tokens 1630 → 1623;
  items 7.10 → 7.87.
- With 3 repos, the repo-clustered CI ([+0.0000, +0.1699]) is not decision-grade.
- The pilot driver had a module-name bug: it imported #43's `run.py` as `run`, which shadowed #42's.
  It crashed before any arm ran, was fixed in the driver only, and was rerun. The frozen scripts are
  unaffected, because as `__main__` they resolve #42's `run` first.
- To keep one model process at a time, the phase 2–3 chain was paused (SIGSTOP) while the pilot was
  scored, then resumed.
