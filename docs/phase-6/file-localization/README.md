# File/module localization feasibility: preregistration (2026-10-17)

Question: is repository-level file/module localization a missing capability in OXIDE,
or would it reproduce the same lexical ranking failures at a coarser granularity? This
follows [../source-body-retrieval](../source-body-retrieval/README.md) (REJECTED,
acdf0ee; its evidence is kept unchanged). There, 45 of 77 vscode dev spans failed
because the gold file is not among the top 20 files under any arm, and Python and the
small TypeScript repositories failed on query discrimination.

This section is written and committed before any arm or signal below is run. Results
are added below it later and do not edit it. This is a bounded feasibility study:
`src/` is not edited, no model is used, and no rejected lexical variant (body
fields, field weights, query pruning) is reopened.

## Pipeline (unchanged in every arm)

Production `oxide-retrieval-v2` (FTS over `document(e)`: name, signature and path;
top 20), `oxide-router-bfs-v2`, `packing.value` and the frozen scorers
(`contextbench-py-scorer-v2` and the TypeScript scorer). Arms change only what
retrieval returns, in the harness process (`scripts/loc_run.py`). Every arm runs on
the same index within one process; the store is built once per corpus.

## Stage 1: headroom (oracle arms)

The oracle is each task's set of verified gold files, G.

- **`none`:** production. Its compat hashes must equal the frozen dev results.
- **`file`:** lexical hits restricted to entities in G. The full production hit list
  is fetched without a cut, every hit outside G is dropped, and the first 20 are kept
  in production order. This is perfect file localization followed by OXIDE's own
  ranking inside the files.
- **`seed`:** G passed as `paths` hints through the existing public API
  (`build_context(paths=...)`, `search(paths=...)`), with production lexical
  unchanged. This is an external localizer feeding OXIDE's existing hint interface.
- **`dir`:** like `file`, restricted to entities in files whose immediate parent
  directory holds a file of G. This is perfect module-level localization.
- **`file@3`, `file@10`:** like `file`, restricted to G plus the first 3 or 10
  non-gold files in production file order (each file ranked by its best entity in
  the full hit list). This is a realistic localizer that places the gold among k
  distractors chosen by the current ranking.

The oracles `file`, `seed` and `dir` are theoretical bounds. `file@k` measures what a
practical method that ranks the gold file somewhere in its top k would get.

## Stage 2: practical signals (only if stage 1 shows headroom)

These are deterministic file rankings built from data the store already has. Each
query uses `terms(query)`, and ties break by path. There are no parameters to tune
beyond those fixed here.

- **`P0 entry`:** production. Each file is ranked by its best entity rank in the full
  hit list. This is the comparator.
- **`P1 vote`:** the sum of the BM25 scores of the file's entities over the full hit
  list. It aggregates the existing name/signature/path terms and serves as a
  comparator; it is not assumed sufficient.
- **`P2 path`:** BM25 (k1 1.2, b 0.75) over file paths only. Each indexed file's
  document is `terms(path)`, its directories plus its filename.
- **`P3 graph`:** `P1(f) + 0.5 * max P1(g)` over the files g that import f or are
  imported by f, using resolved `imports` relations mapped to their files.
- **`P4 dir`:** hierarchical. Each directory is scored by the sum of `P1` over its
  files; files rank by (directory score, `P1`).

Each signal is measured by gold-file recall@{1, 5, 10, 20}: the fraction of a task's
verified gold files in that signal's top k, macro over tasks, overall and per
repository. Only the best signal by recall@10 on the headroom benchmark (ties go to
the lower index) runs end to end, as arm **`loc`**. `loc` is `file@10`'s mechanism with
that signal's top 10 files, and needs no oracle.

## Data

Dev only:

- ContextBench-py `baseline-v5` dev: 15 tasks (fixture, requests, seaborn, flask).
- TypeScript `typescript-v2` dev: 26 tasks (vscode, code-server, darkreader,
  fixture).

The harness drops held-out tasks before indexing. Held-out stays unopened whatever the
outcome: this study can return PROMISING at most, and a promotion would need its own
preregistration.

## Measures

Every arm reports:

- the frozen verified-gold metrics, macro over tasks, overall and per repository:
  - `entry_recall@5/@20` and `entry_mrr`;
  - `routed_coverage`;
  - `span_recall`, `line_recall` and `file_recall` at 256, 1,024 and 4,096 units;
- tasks improved and regressed in a packed metric (`span_recall` or `line_recall` at
  any budget);
- bundle determinism.

**Packed quality decides; file recall alone never does.**

## Criteria and verdicts

Let **gain** be an arm's dev macro change in `line_recall@1024` or `line_recall@4096`
(the larger of the two) against `none`, on one benchmark.

1. **Headroom:** `file` or `seed` has gain ≥ 0.10 on a benchmark, and at least 5 of its
   tasks improve in a packed metric. A benchmark that passes is a *headroom benchmark*.
2. **Practical headroom:** on a headroom benchmark, `file@10`'s gain is at least half of
   the best gain among `file` and `seed`.
3. **Signal:** on a headroom benchmark, some signal `P1`–`P4` raises gold-file
   recall@10 by ≥ 0.10 over `P0`, overall.
4. **End to end:** `loc` has gain > 0.02 (the frozen tolerance) on that benchmark, and
   no repository's `line_recall@1024` or `@4096` falls by more than 0.02.

- **NOT VIABLE:** 1 fails (no headroom), or 2 fails (only perfect localization helps;
  a realistic top-10 localizer brings the ranking failure back), or 3 fails (no
  deterministic structure signal localizes better than the current ranking).
- **PROMISING:** 1 to 4 all hold.
- **INCONCLUSIVE:** 1 to 3 hold and 4 fails, or the deciding gain comes from fewer than
  3 tasks (dev resolution: a single task moving by its whole share shifts a macro mean
  by 1/15 ≈ 0.067 on py and 1/26 ≈ 0.038 on ts).

Stopping rule: stop at the first failed criterion. Stage 2 runs only if 1 and 2 hold.
If the verdict is NOT VIABLE, the investigation closes with a recommendation to move to
packaging instead of further retrieval R&D.

## Failure reading (reported whatever the verdict)

For each oracle arm, each verified span still missed at 4,096 units is classed by the
scorer's own first-loss stage (`entry`, `routing`, `unsupported`, `selection`,
`budget`). This separates two cases:

- *localization would suffice*: the span is packed once the file is known;
- *same failure at a finer grain*: the file is known, but entry ranking, routing or
  selection inside it still loses the span.

## Reproduce

From the repository root, with corpora fetched, capped as for every heavy run:

    systemd-run --user --scope -q -p CPUQuota=200% -p MemoryMax=3G -p MemorySwapMax=0 \
      uv run python docs/phase-6/file-localization/scripts/loc_run.py {py|ts} OUTDIR

# Results (2026-10-17, dev only)

**Verdict: NOT VIABLE**, under the preregistered criteria. Criterion 1 (headroom) holds on
both benchmarks; criterion 2 (practical headroom) fails on both, so stage 2 was not run
and no `loc` arm exists. Held-out was not opened, `src/` is unchanged, and `none`
reproduces the frozen compat hashes on 15/15 py and 26/26 ts dev tasks. Every bundle
in every arm is deterministic. Numbers are measured unless marked *inferred*; the
per-arm results and summaries are in `results/`.

## Stage 1 (dev macro, verified gold; change against `none`)

| Arm | py `line_recall@1024` | py `@4096` | ts `@1024` | ts `@4096` | vscode `@4096` | tasks up / down (py, ts) |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `none` | 0.548 | 0.659 | 0.330 | 0.370 | 0.045 | — |
| `file` (oracle) | +0.051 | **+0.117** | +0.093 | **+0.404** | +0.616 | 11/0, 16/0 |
| `dir` (oracle) | 0.000 | 0.000 | +0.018 | +0.234 | +0.348 | 3/0, 13/0 |
| `seed` (oracle via `paths`) | −0.020 | +0.027 | +0.006 | −0.010 | −0.007 | 1/6, 6/6 |
| `file@3` | 0.000 | +0.007 | +0.013 | +0.036 | +0.057 | 1/0, 5/0 |
| `file@10` | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0/0, 0/0 |

- **Criterion 1 holds:** `file` gains +0.117 on py (11 tasks improve, 4 carry the
  `@4096` gain, flask alone +0.067) and +0.404 on ts (16 tasks improve, 15 carry it).
  The headroom is real and lies at 4,096 units. At 1,024 units it is below 0.10 on
  both benchmarks.
- **Criterion 2 fails:** `file@10`'s gain is 0 on both benchmarks, and even `file@3`
  keeps only 6 % (py) and 9 % (ts) of `file`'s gain. The restriction does change the
  entry lists (`file@10` on 7/15 py tasks, `file@3` on 13/15), but with the gold file
  allowed beside the files the current ranking already prefers, the gold entities
  still rank below those files' entities, inside the top 20 and therefore inside the
  bundle.

## What this answers

- **Theoretical bound vs. practical method.** Perfect file localization would roughly
  double ts packed recall at 4,096 units, and multiply it by 15 on vscode. Placing the
  gold file among the current ranking's top 3 or 10 files recovers almost none of
  that. A localizer helps only if it puts the gold file *above* the files whose
  entities win the existing name/signature/path BM25, which is the existing lexical
  ranking problem again, one level up. That is the "same failure at a different
  granularity" outcome.
- **The `dir` oracle is the one caveat.** Restricting to the gold directory (gold plus
  its sibling files) still gains +0.234 on ts and +0.348 on vscode. So distractors as
  such are not the issue: siblings that do not win the global BM25 are harmless. The
  harmful distractors are exactly the current lexical winners. A directory-level signal
  that excludes them would have to rank the gold directory near the top using
  something other than the same terms. Stage 2 would have tested that; the
  preregistered stopping rule forbids running it here (*inferred*; not measured).
- **The existing `paths` hint does not turn a correct file into packed gold.** `seed`
  reaches the file (ts `routed_coverage` 0.468 → 0.968, `file_recall@4096` → 0.980),
  but packed line recall does not move (ts −0.010 at 4,096; py 6 tasks regress). Of
  ts's missed spans at 4,096 units, 82 are lost at `budget`: the seeded file's
  module-level view is valued 0.9 and spends the budget before the gold symbols. This
  is a selection/packing finding about the hint path, not a localization one.

## Failure reading (verified spans missed at 4,096 units, first-loss stage)

| Arm | py: entry / routing / selection / budget | ts: entry / routing / selection / budget |
| --- | --- | --- |
| `none` | 4 / 5 / 7 / 11 (27 of 47) | 66 / 9 / 7 / 11 (93 of 115) |
| `file` | 1 / 6 / 3 / 9 (19) | 5 / 10 / 8 / 30 (53) |
| `dir` | 2 / 5 / 7 / 13 (27) | 17 / 11 / 14 / 32 (74) |
| `seed` | 0 / 6 / 4 / 16 (26) | 0 / 14 / 4 / 82 (100) |

With the file known (`file`), entry losses nearly vanish (66 → 5 on ts), but 48 of the
53 remaining ts losses come from routing, selection or budget within the known file.
On py, localization would suffice for only 8 of the 27 missed spans. The rest are the
same failures at a finer grain.

## Cost

Restriction is a harness filter over the full hit list. The ts run (six arms on one
index per corpus) peaked at 1,283 MB RSS, with no `default_index_failure`.

## Decision

The investigation closes. Repository-level localization is not a missing capability
that a deterministic signal could cheaply add. Its headroom only appears under a
perfect oracle, and disappears as soon as the gold competes with the files the
current ranking already prefers.

**Recommendation:** move to packaging and stop speculative retrieval R&D. Hierarchical
retrieval is not justified by this evidence. If retrieval is ever reopened, there are
two concrete leads, each needing its own preregistration:

- a directory-level localizer that does not reuse the entity terms;
- why the `paths` hint's file view spends the budget.

## Reproduce

    systemd-run --user --scope -q -p CPUQuota=200% -p MemoryMax=3G -p MemorySwapMax=0 \
      uv run python docs/phase-6/file-localization/scripts/loc_run.py py OUT/py
    # same for ts, then:
    uv run python docs/phase-6/file-localization/scripts/loc_sum.py OUT/py py
