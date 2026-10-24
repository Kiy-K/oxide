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
| `build_context` (no judge) | 14.5 ms | 12.2 ms |
| `pack`, no judge | 7.59 ms | 5.58 ms |
| `pack`, heuristic replay (same bundle) | 7.66 ms | 5.68 ms |
| `judged()` validation alone | 0.025 ms | 0.025 ms |

The seam itself costs about 25 µs a request, ≤ 0.1 ms in `pack` (≤ 1% of
`build_context`). Re-run 2026-10-24 after the capsule change; the first run
(22.6 / 20.3 ms `build_context`) was slower across every row, which is machine
variance, not the change. The reversed replay (12.3 / 6.4 ms) packs different, larger items; that
is rendering work, not interface cost. *Inferred:* the no-judge path adds one list
comprehension over the pool, below `judged()`'s cost. A pre-change `pack` was not timed.
A real judge's own latency is not measured here and would dominate.

## Candidate Capsule v1 (`src/oxide/core/capsule.py`)

The second step (2026-10-24) settles what a real judge would be shown and how long
the request waits for it. It is still infrastructure only: no model, no quality claim,
and nothing in `src/` uses the network (the measurement script fetches tiktoken's
encodings once). The seam above is unchanged; the capsule sits behind it as one
`DecisionProvider`.

| Piece | Contract |
| --- | --- |
| `build(query, derivation, candidate, source)` | The `oxide-capsule-v1` capsule of one candidate, or `None` when it cannot be represented within bounds |
| `Capsule` | Frozen, typed fields; `canonical` is compact key-sorted UTF-8 JSON (`allow_nan=False`), `digest` its sha256 |
| `CapsuleJudge(name, query, derivation, source, adapter, deadline=2.0)` | A request-scoped provider: builds the capsules, makes **one** adapter call under the deadline, maps digests back to keys. Identity `<name>@oxide-capsule-v1` |
| `Adapter` | `(capsules) -> {capsule digest: value in [0, 1] or None}`, the shape an external judge implements |
| `Recorded(judgments)` | The deterministic adapter fake: answers from judgments recorded by digest; also the replay of a captured run |

`Candidate` gained three evidence fields that selection never reads: the lexical
`score`, `seeds` (`kind:hint`) and route `origins`, filled by `build_context` from
what retrieval and routing already computed. Without a judge the bundle is unchanged.

### Fields

| Family | Fields | Bound |
| --- | --- | --- |
| Identity/version | `schema`, `derivation` (parser/extraction identity), `key`, `source_digest` (captured file digest), `digest` | key ≤ 1,024 bytes, never clipped |
| Task | `query` (clipped), `query_digest` (of the whole query) | 1,024 bytes |
| Subject | `category`, `kind`, `name`, `signature`, `path`, `lines`, `test` | 512 bytes per text field |
| Source | `excerpt` of the subject's own byte range, `source_bytes` (its full length) | 4,096 bytes, cut after the last whole line that fits, else at the limit |
| Retrieval | `lexical_rank`, `lexical_score` (6 decimals), both `null` when not a lexical hit, never 0; `seeds`; `hinted` | 4 seeds × 512 bytes |
| Structure | `depth`, `origins` (`entry`, `root`, `child:<k>`, `scope:<k>`, `<relation>:<in\|out>:<k>`), `origins_omitted` | 8 origins × 512 bytes |
| Limits | `truncated`: every clipped field, in a fixed order | whole canonical capsule ≤ 16,384 bytes |

Excluded on purpose: the heuristic value and its basis, any selection or packing
outcome, gold, holder sets and oracle order (SPEC § Candidate capsule). Repository
identity is not a field. Neighbors appear only as keys inside `origins`; no other
entity's source is hydrated, so no excluded or secret file can enter through a
neighbor. The excerpt comes from the snapshot's captured bytes (`View.source`
verifies the digest), never the worktree.

### Guarantees (tested in `tests/python/test_capsule.py`, 16 tests)

| Condition | Effect |
| --- | --- |
| Same query, derivation, candidate and bytes | Byte-identical canonical form and digest |
| Any evidence changes (source bytes, query, rank, …) | A new digest: a recorded judgment no longer applies (`missing`) |
| Recorded judgments replayed | The identical bundle; heuristic values replayed by digest pack the heuristic bundle at budgets 0 to 4,096 |
| Pool order permuted | The adapter receives the same capsules in the same (digest) order, so batch position carries no pool rank |
| Field over its bound | Clipped on a UTF-8 boundary and named in `truncated`; the query keeps the digest of its full text |
| Key over 1,024 bytes, or canonical form over 16,384 (e.g. control characters that JSON-escape six-fold) | Not sent; that candidate keeps the heuristic (`<id> missing`). No sendable capsule: the adapter is not called |
| Adapter slower than `deadline` | Abandoned; heuristic for all (`failed (TimeoutError)`) |
| Adapter raises / returns a non-mapping / an unknown digest (or subject keys instead of digests) | Heuristic for all (`failed (<Error>)`, `TypeError`, `LookupError`) |
| A NaN, out-of-range or `None` value | Per-candidate fallback, as in the seam (`invalid`, `abstained`) |

The deadline runs the adapter on a daemon thread and stops waiting; Python cannot
stop the thread, so a real adapter must also bound its own I/O. The default request
path never constructs a `CapsuleJudge` and starts no thread.

### Cost on real pools

[results/capsules.json](results/capsules.json), from `scripts/capsules.py`: the
Python ContextBench **dev** split (15 requests; held-out stays sealed, the script reads
no gold), one capsule per candidate `pack` would ask about. x86_64, Python 3.13.17,
under `CPUQuota=200%`. With 15 requests, p95 is the maximum.

| Per request | Median | Max |
| --- | ---: | ---: |
| Pool / capsules sent | 64 / 60 | 64 / 64 |
| Build all capsules | 4.7 ms | 13.5 ms |
| `CapsuleJudge.judge`, empty recorded adapter (build + thread + validation) | 4.7 ms | 11.0 ms |
| Canonical bytes | 128,919 | 245,423 |
| Tokens, o200k_base / cl100k_base | 38,349 / 37,890 | 67,271 / 66,503 |

| Per capsule (707) | Median | p95 | Max |
| --- | ---: | ---: | ---: |
| Canonical bytes | 2,033 | 6,169 | 6,655 |
| Tokens, o200k_base | 597 | 1,663 | 2,025 |

No capsule was unsendable; the 16 KiB bound never fired. 245 capsules (35%) carry a
truncation: `query` 186, `excerpt` 91, `origins` 33, `signature` 3. ContextBench
queries are issue texts, so the 1 KiB query bound cuts most of them.

Reading (*inferred* from the bounds): the query is repeated in every capsule, so up to
1 KiB of a median 2 KiB capsule is the same task text, and a whole request is ~38k
tokens. The canonical form is per candidate by
design (it is the replay and dataset unit). A model-facing renderer can state the task
once per request; that is a renderer decision for the study, not a capsule change.
Building costs about 5 ms a request, small next to a remote judge's latency but not
negligible against a 12–15 ms `build_context` (fixture repository).

### Limitations

- A file candidate's excerpt is the first 4,096 bytes of the file, not the
  module-level view `pack` would render.
- `lines` and the excerpt describe the subject only: no callers, callees or tests are
  hydrated, only named in `origins`.
- Query context beyond the query is limited to the seeds a hint produced on the
  candidate itself; unmatched hints and the bundle's other hints are absent.
- Lexical rank and score are features: a judge may simply learn them. A study must
  ablate them (rank-leakage control).
- The deadline is wall-clock on a thread; a stuck adapter keeps its thread until the
  process exits.
- The schema is a proposal at `oxide-capsule-v1`: fields and bounds were chosen for
  the smallest useful contract, not tuned. No renderer for a particular model exists;
  a model-specific rendering needs its own versioned manifest (SPEC).

## Before testing a real judge

Done: the seam (§ Interface), the capsule and the deadline (§ Candidate Capsule v1).
Remaining, in order:

1. **Preregistration** (BOOTSTRAP § Baselines and promotion), written before any judge
   number: the judge and its version, the renderer (task once per request or per
   capsule) with a versioned manifest, a per-request token and latency budget sized
   from § Cost on real pools, the metric (macro line recall at matched budget against
   production and graph-v3's ceilings: Python @4,096 production 0.659, `oracle_order`
   0.792, `oracle_pick` 0.886; TypeScript 0.370 / 0.423 / 0.428, at most +0.058
   headroom, so Python is the informative language), and the decision rule. Dev only
   to develop; the held-out split opens once.
2. **Controls**: a random-value judge and a permuted-capsule judge at the same budget,
   a rank-ablated capsule (no `lexical_rank` / `lexical_score`) to separate judgment
   from rank copying, and repeatability (identical capsules, repeated calls) and
   calibration checks (SPEC § Evaluation, Decision row). Record every judgment by
   digest so a run replays through `Recorded`.
3. **Adapter** outside the core: the external call, its own I/O deadline, disclosure
   rules (source leaves the machine; explicit opt-in per repository, SPEC §
   Security) and provider/model identity in `name`.
4. **Identity in bundles**, if judged bundles become a product path: `versions` must
   record the provider and capsule schema, and the selector version must distinguish
   judged runs.
5. **No public configuration** until a judge passes those gates (no speculative flags).

## Reproduce

```sh
uv run pytest tests/python/test_judgment.py
uv run python docs/phase-6/decision-seam/scripts/overhead.py 500
uv run pytest tests/python/test_capsule.py
uv run --with tiktoken python docs/phase-6/decision-seam/scripts/capsules.py \
  docs/phase-6/decision-seam/results/capsules.json  # needs the ContextBench corpora
```
