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
