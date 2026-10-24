# decision-seam: optional DecisionProvider behind `packing.value` (2026-10-24)

BOOTSTRAP § Remaining 2. This is **infrastructure verification only**. It claims no
retrieval or context-quality change; no judge was evaluated. Numbers are measured unless
marked *inferred*.

## Interface (`src/oxide/core/packing.py`)

| Piece | Contract |
| --- | --- |
| `DecisionProvider` (Protocol) | `identity: str`; `judge(candidates) -> Mapping[key, float \| None]`. Request-scoped: it already holds the query. A value in [0, 1] or `None` (abstain) per candidate key |
| `Replay(identity, judgments)` | The deterministic fake: returns recorded judgments for the requested keys only. Also the replay of a captured judged run (SPEC § Reproducibility) |
| `judged(provider, candidates)` | Validation. Returns value and basis per candidate |
| `pack(..., judge=None)` | Asks the provider **once**, with the candidates that have a source and are not containers (unparsed files, modules, repository). Pool order, at most `GRAPH_NODES` |

The judgment replaces only `value`. Deterministic policy keeps everything else: the
`paths`-hint tier, `INCLUDE_FLOOR` (applied to the judged value), the `MAX_ITEMS` cap,
ties broken by pool position, prerequisite headers, reduced views and the budget. A judge
can therefore reorder, lift a routed candidate above the floor, or drop one below it,
which is the `oracle_order` / `oracle_pick` shape graph-v3 measured. It cannot force
inclusion or exceed the budget.

It is core-only. `Repository.build_context`, the CLI, MCP and `oxide/__init__.py` are
unchanged, so the public API and versioned formats (selector `oxide-select-greedy-v4`,
payload v3, units v1) are untouched. Evaluations inject a provider the way graph-v3's
`order_arm.py` patches `packing.pack`.

## Fallback guarantees (tested in `tests/python/test_judgment.py`)

| Condition | Effect | Reason suffix |
| --- | --- | --- |
| No provider | Byte-identical to the heuristic path | none |
| Key not returned | That candidate keeps the heuristic value | `<id> missing` |
| `None` | Heuristic for that candidate | `<id> abstained` |
| NaN, ±inf, outside [0, 1], bool, non-number | Heuristic for that candidate | `<id> invalid` |
| Provider raises (any `Exception`) | Heuristic for **all** | `<id> failed (<ErrorType>)` |
| Result is not a mapping | Heuristic for all | `<id> returned no mapping` |
| A key outside the requested set | Heuristic for all (correlation failure) | `<id> judged an unknown candidate` |

A valid judgment's reason is `value 0.90, judged by <id>`. A judged candidate below the
floor is omitted as `value 0.10 below floor, judged by <id>`. Replaying the heuristic's
own values packs the same items and omissions at every budget tested (0 to 4,096).

## Compatibility (measured)

With no judge configured:

- `mise run verify`: 330 passed, 7 skipped (TypeScript gate). This includes the frozen
  Phase 3 baseline (`test_baseline.py`) and the Python ContextBench gate (`baseline-v13`).
- `mise run contextbench-ts`: 17 passed (`typescript-v10`).

No frozen artifact was regenerated.

## Cost ([results/overhead.txt](results/overhead.txt))

`scripts/overhead.py` uses `fixtures/py_repo` and one real pool (48 candidates, 41 asked).
Medians of 500 calls, x86_64, Python 3.13.17, under `CPUQuota=200%`:

| | @1,024 | @4,096 |
| --- | ---: | ---: |
| `build_context` (no judge) | 22.6 ms | 20.3 ms |
| `pack`, no judge | 9.72 ms | 7.41 ms |
| `pack`, heuristic replay (same bundle) | 9.91 ms | 7.45 ms |
| `judged()` validation alone | 0.030 ms | 0.030 ms |

The seam itself costs about 30 µs a request, ≤ 0.2 ms in `pack` (≤ 1% of
`build_context`). The reversed replay (15.2 / 8.7 ms) packs different, larger items; that
is rendering work, not interface cost. *Inferred:* the no-judge path adds one list
comprehension over the pool, below `judged()`'s cost. A pre-change `pack` was not timed.
A real judge's own latency is not measured here and would dominate.

## Before testing a real judge

1. **Capsule.** The provider receives `Candidate`s: entity key, kind, name, signature,
   source ref, depth, rank, seeded and hinted. It does not receive source text or a
   versioned `CandidateCapsule` (SPEC § CandidateCapsule). A real judge needs a bounded,
   versioned capsule with a digest correlated in the result, plus scope/secret filtering
   on hydration.
2. **Deadlines.** The call is synchronous with no timeout. The adapter must enforce its
   deadline and raise (→ wholesale fallback), or the seam gains a bounded deadline.
3. **Identity in bundles.** The judge identity appears only in per-item and omission
   reasons. If judged bundles become a product path, `versions` must record the provider,
   and the selector version must distinguish judged runs.
4. **Evaluation protocol** (BOOTSTRAP § Baselines and promotion). Preregister the gates
   before any judge number. Compare at matched budget against production and against
   graph-v3's ceilings (Python @4,096: production 0.659, `oracle_order` 0.792,
   `oracle_pick` 0.886; TypeScript 0.370 / 0.423 / 0.428). Use dev only to develop and
   open the held-out split once. The judge sees no gold, holder sets or oracle order. Add
   random/permutation controls and repeatability/calibration (SPEC § Evaluation, Decision
   row). TypeScript headroom is at most +0.058, so Python is the informative language.
5. **No public configuration** until a judge passes those gates (no speculative flags).

## Reproduce

```sh
uv run pytest tests/python/test_judgment.py
uv run python docs/phase-6/decision-seam/scripts/overhead.py 500
```
