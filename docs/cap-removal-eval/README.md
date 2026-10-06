# #43 cap removal at matched tokens

Status: **REJECT** (Q1 and E1 fail; §Result). Production is unchanged.

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

## Result (full run, 2026-10-06): REJECT

`results/result.txt`, `result.json`, `per_task.tsv`, `packs.jsonl`, `parity_p1.tsv`, `rss_o1.tsv`.
All numbers are **measured** unless marked *inferred*.

149 fresh tasks: 80 held-out (8 repos) and 69 ContextBench (37 repos). B vs A, at matched tokens
(1735 → 1730), repo-clustered paired bootstrap:

| stratum | coverage A → B | Δ [95 % CI] | up/down | rel/used [95 % CI] |
|---|---|---|---|---|
| pooled | 0.2656 → 0.3036 | +0.0380 [−0.0063, +0.0846] | 30/8 | +6.7 % [−5.3 %, +19.6 %] |
| held-out | 0.3835 → 0.4401 | +0.0567 [−0.0233, +0.1430] | 10/3 | −1.1 % [−29.1 %, +20.6 %] |
| ContextBench | 0.1290 → 0.1454 | +0.0163 [+0.0037, +0.0345] | 20/5 | +8.8 % [−4.6 %, +24.3 %] |

| gate | result | pass |
|---|---|---|
| Q1 | pooled CI lower bound −0.0063 ≤ 0 | ❌ |
| Q2 | no negative stratum (+0.0567, +0.0163) | ✅ |
| E1 | lower bound −5.3 % < −5 % | ❌ |
| R1 | 8 regressed ≤ 30 improved; every regression explained below | ✅ |
| O1 | latency median Δ +2.97 ms > 2.56 ms (5 % of 51.2 ms median search) ❌; RSS +0.72 %, +0.25 %, +0.43 % ✅ | ❌ |
| P1 | parity 149/149 (rerun, see below); B repeats 149/149 | ✅ |

Validity holds (80 held-out, 69 ContextBench, 8 held-out repos, P1). Q1 and E1 fail → **REJECT**.

Reading: cap removal points the same way as #42 and the pilot (30 up / 8 down, and ContextBench alone
has a CI above 0), but on fresh tasks the pooled effect is not decision-grade. The held-out stratum
spans only 8 repos, so its clustered CI is wide. The task-level bootstrap (descriptive) is
[+0.0070, +0.0715].

### R1: every regression

Mechanism (measured from the packs): B admits primaries that A's 5-primary / 2-per-file caps
would have excluded, all low-scoring and none gold-bearing. At matched tokens that budget comes out of
either A's graph-expanded dependency items or the window of a later gold primary.

- **Displaced dependency (6).**
  - `ripgrep-ab4665a1`: `ErrorKind.description` (1 gold line) lost to three extra primaries; 0.80 → 0.70.
  - `clap-6c2cff66`: `write_opts_of` (the only gold line) lost; B added `write_positionals_of`
    and two `__module__` items; 1.00 → 0.00.
  - `…c…0dde5323` (ponyc): `ponyc_opt_process` (47 gold lines) lost to `lib/gbenchmark/*` primaries.
  - `…go…b5ad2c2c` (navidrome): `CookieExpiry` (1 gold line) lost to `CODE_OF_CONDUCT.md` and two
    module items.
  - `…rust…15d38459` (clap): `Error` (8) and `Result` (1) lost to one `clap_complete` module item.
  - `…javascript…cbea412a` (express): `tryRender` (7) and `View` (44) lost to two `examples/` modules.
- **Shrink-to-fit truncation (2).**
  - `rayon-9461f7bb`: four extra `ThreadPool` primaries from one file (no per-file cap) left less budget,
    so `default_global_registry` was cut from lines 199–220 to 199–208 (11 → 0 gold lines).
  - `…c…5e659108` (ponyc): `check_partial_function_call` cut from 31–72 to 39–49 (42 → 11 gold lines).

*Inferred:* the caps act as a cheap guard for one-hop dependencies. Removing them trades those for
low-value primaries on these tasks.

### Run log and disclosed deviations

- **Reboot (2026-10-05).** The machine rebooted during the ContextBench draw. The chain resumed with
  the same frozen scripts. Finished stages are idempotent (`.done` markers), and the deterministic draw
  (seed 43) replayed to the same position.
- **Manual stop (2026-10-05 evening) and resume (2026-10-06).** Stopped by decision at CB draw 49/69.
  The one partially indexed CB checkout was deleted so it re-indexed from scratch. Same scripts.
- **Data move and P1 rerun.** During the stop, the eval caches moved from `~/.cache/` to
  `~/Projects/oxide-eval-data/` with symlinks back. Indexes record their canonicalized root, so the
  129 indexes built before the move no longer matched their resolved path. In the as-run P1, both the
  frozen and the research binary refused them (`index_incompatible`), giving parity 20/149 and
  disposition INCONCLUSIVE (`results/run1-asrun/`). The arms were unaffected: the pack harness opens
  the store directly, and every A pack is non-empty. Fix: `meta.root` of those 129 indexes was rewritten
  to the resolved path (no other change), and only `run.py parity` and `score.py` were rerun. P1 then
  passed 149/149. This was decided after the scored results were seen. It cannot change Q1–E1, R1 or
  O1, whose inputs did not change.
- **Concurrent work.** TreeIndex Phase 0 (Voyage API calls and tree builds) ran alongside the
  untimed task generation. It was SIGSTOPped from before `run.py parity` until after `run.py rss`
  (18:02–18:05), so it did not overlap the timed stages. The O1 median search time (51.2 ms, vs 19.3 ms
  in #42's amended run) is reported as measured. Stopped processes still hold memory, but VmHWM is
  per-process.
