# Explicit path-hint utility: preregistration (2026-10-17)

Question: when an agent passes a correct repository path as a `paths` hint, does
OXIDE turn it into useful packed context, and if not, what is the smallest
maintainable fix? This is the second lead of
[../file-localization](../file-localization/README.md) (NOT VIABLE, 4871e58; its
evidence is kept unchanged). General retrieval R&D is closed for this release; this
study only touches what selection does with an explicit, valid path hint.

This section is written and committed before any variant below is run. Results are
appended below it later and do not edit it.

## Contract and diagnosis (measured on dev before this preregistration)

- **Contract** (`docs/public-api.md`, SPEC § Retrieval, § TreeRouter): a path hint
  resolves to a `file:<path>` structural seed. Seeds are entry points; they enter
  TreeRouter and compete for the same budget. Nothing promises that a hinted file's
  lines are packed. So this is not a contract defect.
- **Behavior:** in the localization study's `seed` arm (verified gold files as
  `paths`), routing reaches the file (ts `routed_coverage` 0.468 → 0.968), but
  `line_recall@4096` does not move (ts −0.010, py +0.027, 6 py tasks regress). Of the
  ts verified spans missed at 4,096 units, 76 are lost at `budget: omitted, budget`.
- **Where the budget goes** (`scripts/probe.py ts`, the 26 dev tasks at 4,096 units,
  gold hinted): 48 % of packed units are lexical hits in *other* files, 35 % the
  hinted file's module-level view (valued 0.9 as a seed), 6 % ancestor headers of
  other files' hits. The hinted file's own declarations reached by routing get 0.1 %:
  they are children of the seed at depth 1, and `packing.value` gives them 0.45,
  below every lexical hit anywhere in the repository (0.5–0.85).
- **Reading:** an implementation defect in selection policy. The hint says where the
  relevant code is, yet the selector packs the hinted file's least specific part
  (module-level glue) first and its declarations last, after unrelated lexical hits.

## Variants (selection only; retrieval, routing, scorers unchanged)

A candidate is *in a hinted file* when its source file resolved from a `paths` hint
(not `changed`, not `selection`).

- **`prod`:** production `oxide-select-greedy-v3`.
- **`tier`:** candidates in a hinted file form a precedence tier packed before all
  other candidates. Inside and outside the tier, the existing value, floor (0.4),
  item cap, header-view reduction and graph-order tie-break are unchanged. Symbols
  below the floor (routed depth 2) stay out, so a hint never means "the whole file".
- **`tier-outline`:** `tier`, plus the hinted file's own module-level view is reduced
  to its header view (its first module-level run, normally the imports) when it has
  more than one run.

No numeric parameter is introduced or tuned.

## Conditions

All dev tasks of each benchmark, the frozen task `query` and `symbols` hints, plus:

- **`U` (unhinted):** no `paths`. Every variant must equal `prod` here by
  construction; this is the regression control.
- **`H` (correct hint):** the task's verified gold files as `paths`, the agent that
  knows where the code lives.
- **`N` (noisy hint):** `H` plus the first non-gold file in production lexical order
  (the file the current ranking prefers), an agent that also names one wrong file.
  This measures displacement by a wrong hint.

## Data

Dev only, as the earlier studies:

- ContextBench-py `baseline-v5` dev: 15 tasks (fixture, requests, seaborn, flask).
- TypeScript `typescript-v2` dev: 26 tasks (vscode, code-server, darkreader,
  fixture).

The harness drops held-out tasks before indexing. Held-out is opened at most once,
for the confirmation below, after the chosen variant is frozen in production code.

## Measures

The frozen verified-gold metrics, macro over tasks, overall and per repository:
`span_recall`, `line_recall`, `file_recall` and `gold_unit_share` at 256, 1,024 and
4,096 units; tasks improved/regressed in a packed metric; bundle determinism and
`used <= budget`; `build_context` wall time per call (median over the run).

## Criteria (dev)

Let **gain(X, Y)** be the dev macro change of `line_recall@1024` or `@4096`, the
larger, from Y to X on one benchmark.

1. **Utility over the current hint:** gain(variant·H, prod·H) > 0.02 on both
   benchmarks and ≥ 0.05 on at least one, carried by ≥ 3 tasks.
2. **Hints help at all:** variant·H beats prod·U in `line_recall@4096` by > 0.02 on
   both benchmarks.
3. **No hinted regression:** no repository's `line_recall@1024` or `@4096` under H
   falls by more than 0.02 against prod·H.
4. **Bounded displacement:** variant·N is not below prod·N by more than 0.02 in
   `line_recall@1024` or `@4096` on either benchmark.
5. **Unhinted unchanged:** variant·U equals the frozen dev compat hashes (entries,
   route, payloads at every budget) on every dev task.
6. **Determinism and cost:** every bundle deterministic and within budget; median
   `build_context` time under H within +10 % of prod·H.

The chosen variant is the one passing 1–6 with the larger mean of its criterion-1
gains over the two benchmarks; a tie goes to `tier` (fewer changes).

## Held-out confirmation (once, after the choice is implemented)

With the chosen variant implemented in `src/`, `prod` and the variant run under U and
H on held-out tasks only. It confirms when gain(variant·H, prod·H) > 0.02 on both
benchmarks, variant·U reproduces the frozen held-out compat hashes, and every bundle
is deterministic.

## Verdicts

- **PROMOTE:** a variant passes 1–6 on dev and the held-out confirmation, and
  `mise run verify` passes (frozen baselines included).
- **REJECT:** no variant passes 1, 2 or 3, or one fails 5.
- **INCONCLUSIVE:** a variant passes 1–3 and 5 but fails 4 or 6, or the dev gain
  comes from fewer than 3 tasks, or the held-out confirmation fails.

On REJECT or INCONCLUSIVE, production stays unchanged and the limitation is
documented in `docs/public-api.md`.

## Reproduce

    systemd-run --user --scope -q -p CPUQuota=200% -p MemoryMax=3G -p MemorySwapMax=0 \
      uv run python docs/phase-6/path-hint-utility/scripts/hint_run.py {py|ts} OUTDIR
    uv run python docs/phase-6/path-hint-utility/scripts/hint_sum.py OUTDIR {py|ts}

# Results (2026-10-17)

**Verdict: PROMOTE `tier-outline`** as `oxide-select-greedy-v4`. Both variants pass dev
criteria 1–6. `tier-outline` has the larger mean criterion-1 gain and is chosen by the
preregistered rule. It is implemented in `src/oxide/core/packing.py` (a `hinted`
candidate flag set by `api.build_context` from resolved `paths` seeds). The production
code reproduces the harness arm exactly: the compat hashes of `outline.H` and
`outline.N` equal production H and N on 15/15 py and 26/26 ts dev tasks. The held-out
confirmation passes. Numbers are measured; summaries are in `results/`.

## Dev (macro over tasks with verified gold)

| Arm | py `line@1024` | py `line@4096` | py `line@256` | ts `line@1024` | ts `line@4096` | ts `line@256` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| prod·U (unhinted) | 0.548 | 0.659 | 0.180 | 0.330 | 0.370 | 0.195 |
| prod·H (hint today) | 0.528 | 0.685 | 0.108 | 0.336 | 0.360 | 0.182 |
| tier·H | 0.528 | 0.782 | 0.337 | 0.360 | 0.575 | 0.194 |
| **outline·H** | 0.538 | **0.785** | 0.357 | 0.380 | **0.626** | 0.207 |
| prod·N (gold + 1 wrong file) | 0.528 | 0.685 | 0.065 | 0.336 | 0.360 | 0.167 |
| outline·N | 0.528 | 0.695 | 0.065 | 0.342 | 0.472 | 0.174 |

| Criterion | `tier` py / ts | `outline` py / ts |
| --- | --- | --- |
| 1 gain over prod·H (carriers) | +0.097 (4) / +0.215 (9) | +0.100 (5) / +0.266 (10) |
| 2 H over prod·U at 4,096 | +0.124 / +0.205 | +0.127 / +0.256 |
| 3 worst repository against prod·H | 0.000 / 0.000 | −0.003 (seaborn @1024) / 0.000 |
| 4 noisy hint against prod·N | 0.000 / +0.008 | 0.000 / +0.006 |
| 5 unhinted equals frozen | by construction; production 15/15, 26/26 | same |
| 6 deterministic, within budget; time ratio to prod·H | yes; 0.84 / 0.98 | yes; 0.81 / 0.97 |

Tasks improved / regressed in any packed metric against prod·H: `outline` 11 / 1 (py)
and 12 / 2 (ts). By repository at 4,096 units under H: vscode 0.038 → 0.377,
darkreader 0.103 → 0.795, code-server 0.148 → 0.579, requests 0.541 → 0.783; seaborn
and flask stay where they were (gold beyond the hinted file's top-level declarations).
The hinted bundle's `gold_unit_share@4096` rises from 0.219 to 0.301 (py) and 0.159 to
0.268 (ts), so the budget moves to the gold, not just more of the file.

## Held-out confirmation (run once, production code against 7b80ba0)

| | py `line@1024` | py `line@4096` | ts `line@1024` | ts `line@4096` |
| --- | ---: | ---: | ---: | ---: |
| prod·U (both trees) | 0.129 | 0.235 | 0.139 | 0.239 |
| old prod·H | 0.127 | 0.273 | 0.227 | 0.420 |
| new prod·H | 0.213 | **0.545** | 0.384 | **0.728** |

- gain(new·H, old·H) is +0.272 (py) and +0.308 (ts), both > 0.02.
- Unhinted output reproduces the frozen held-out compat hashes on 29/29 py and 63/63
  ts tasks on both trees, and every bundle is deterministic.
- Every repository gains at 4,096 units: xarray 0.206 → 0.384, pytest 0.369 → 0.772,
  mui 0.442 → 0.775, vue 0.346 → 0.573.
- 24/29 py and 50/56 ts tasks improve in some packed metric. 2 py and 12 ts tasks lose
  in some packed metric, mostly at a single budget.

## What changed and what did not

- With no `paths` hint, every bundle is byte-identical to before. All frozen baselines
  reproduce exactly. The selector version bump re-freezes ContextBench-py as
  `baseline-v6` and TypeScript as `typescript-v3`, with only `versions.selector`
  changed.
- `changed` and `selection` hints are not affected. Retrieval, routing, indexing and the
  store are not touched, so the incremental-indexing guarantees are unchanged.
- Cost: no extra store reads. Median `build_context` time under H was at or below
  prod·H on every run (the ratio < 1 is noise, *inferred*). Peak RSS of the ts dev run
  was 1,249 MB.

## Remaining limitations

- A hint is a priority, not a guarantee. Spans in methods two levels deep (routed
  depth 2, below the 0.4 floor) and declarations beyond the router's per-region fanout
  of 16 children are still not packed. This is most of what is left on seaborn and
  flask, and in the 14 ts `routing: not routed` losses.
- A wrong hint costs budget. Under N, the wrong file's declarations are packed before
  the lexical evidence too. That is no worse than today on dev (criterion 4), but under
  N the gain over prod is +0.010 (py) and +0.112 (ts) at 4,096 units, instead of
  +0.100 and +0.266 with a correct hint.
- The hinted file's module-level code beyond its first run is no longer packed for
  that file. This is the cause of the seaborn −0.003 at 1,024 units.
