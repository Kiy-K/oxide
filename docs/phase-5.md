# Phase 5 — DecisionBench, JEV validation and confidence calibration

Status: **complete** (2026-10-10) on `rewrite/v2`. No model was trained or
deployed. The deterministic heuristic stays the default DecisionProvider.
Decisions stay inside SPEC, [ADR-0002](adr/0002-ladybugdb-knowledge-store.md)
and [ADR-0010](adr/0010-domain-identity-and-source-capture.md); no ADR
status changed. Protocol:
[phase-5/preregistration.md](phase-5/preregistration.md) (frozen before any
result, with one dated amendment).

> Can an external judge (JEV) improve OXIDE's ContextBundle over the
> deterministic heuristic, at acceptable latency, privacy and cost?

## What exists

| Piece | Where | Tests |
| --- | --- | --- |
| DecisionBench v1 records, labels, validation, leakage checks, judgment statistics | `runtime/src/decisionbench.rs` | unit tests there, `runtime/tests/phase5_decisionbench.rs` |
| Confidence floors apply only to calibrated confidence (`Fallback::Uncalibrated`) | `kernel/src/decision.rs` | `kernel/tests/routing_boundary.rs` |
| JEV HTTPS transport, 429/529 bounded retries, request and token caps, score/distribution check, validity analysis | `runtime/src/jev.rs` | unit tests there (local mock HTTP server) |
| Configurable LadybugDB buffer pool; single-threaded publish (deterministic BM25) | `runtime/src/storage/mod.rs` | the ContextBench run and its replay (opt-in, machine-local) |
| Fixture DecisionBench with recorded live JEV answers (frozen, offline) | `runtime/tests/phase5_decisionbench.rs`, `docs/phase-5/decisionbench-v1/fixture/` | reproduces byte for byte |
| Fallback matrix (14 conditions) | `runtime/tests/phase5_decisionbench.rs` | same |
| ContextBench DecisionBench (opt-in, machine-local records) | `runtime/tests/phase5_contextbench.rs`, `docs/phase-5/decisionbench-v1/contextbench/` | `#[ignore]`d |

New dependency: `ureq =3.4.2` (rustls; the runtime had no HTTP client).

## DecisionBench v1 (`oxide-decisionbench-v1`)

A record is one canonical capsule exactly as runtime inference rendered it
(`Capsule::render(false)`; its `capsule_digest` is `Capsule::digest`, the
string judgments and traces correlate by). Kept outside the capsule:
split, repository and task, subject kind (candidate, neighbor, region), a
label with annotations, strata, observation (stage, retrieval and router
versions, channel mode, position, inclusion probability) and policy outcomes
(heuristic value, planned). `validate` rejects a record whose bytes,
digest, version, top-level keys or question disagree. It also rejects a
relevant/irrelevant label without an agreeing non-teacher annotation: a
JEV or model annotation can be stored but never decides a label.

Labels (head `gold_context`):

- `gold-membership-v1` (candidates, neighbors): `relevant` if gold;
  `uncertain` if it encloses gold or comes from an unverified span;
  otherwise `irrelevant`, which means "absent from incomplete gold".
- `subtree-contains-gold-v1` (regions): `relevant` if the subtree holds
  verified gold.
- `cb-span-innermost-v1`: a ContextBench span names the innermost symbols
  intersecting it, else its file. It is *verified* only when its
  whitespace-collapsed text hashes to the benchmark's recorded content
  (379 of 441 spans match the base-commit bytes exactly).

Strata are derived from the run, never labels: `entry:lexical`,
`lexical_top5`, `entry:seed`, `routed_only`, `container`, `test`,
`hard_negative`, `misleading_lexical`, `same_file_as_gold`,
`neighbor_of_gold`, `relevant_structural_neighbor`, `useful_descendants`,
`no_gold_branch`, `promising_no_gold`. Every negative comes from the
routed universe; there are no random negatives. Only subjects that routing
observed exist. Unrouted gold is reported (`gold_unobserved_by_routing`)
and never counted as a negative. "Useful only in combination" has no
annotation source and stays unrepresented.

### Data and splits

| | Tasks | Repositories | Records |
| --- | --- | --- | --- |
| fixture (dev, CI) | 7 | `fixtures/py_repo` | 347 |
| dev | 14 | keras, sphinx, sympy | 1,563 |
| calibration | 14 | ansible, matplotlib, requests, pytest, qutebrowser, yt-dlp | 1,537 |
| test | 24 | astropy, django, transformers, openlibrary, langchain, xarray, scikit-learn | 2,639 |
| train-reserved | 0 | — | — |

Tasks are the 52 ContextBench Python tasks whose public repository was
checked out at the benchmark base commit on this machine
(`decisionbench-v1/contextbench-tasks.json`: queries, spans and content
digests only, no source). Splits are by repository
(`decisionbench-v1/splits.json`), frozen before any metric. Leakage checks
(no repository or task in two splits, no near-duplicate query across
splits, every record in its repository's split) pass. The task, split and
preregistration files are hash-pinned in CI.

Annotation quality: fixture gold was hand-written by OXIDE maintainers.
ContextBench gold is benchmark-curated and independent of OXIDE and JEV,
but incomplete for read-required context, so `irrelevant` is an upper
bound on true negatives. No human audit was done in this phase. Records
and JEV exchanges hold third-party source, so they stay machine-local
(`OXIDE_DECISIONBENCH_DATA`); the committed manifest pins each task's
snapshot and records SHA-256.

## JEV: live compatibility VERIFIED (`jev-1.13.0`, 2026-10-09)

Docs re-read on 2026-10-09 (`docs.typesafe.ai/api`, `/models`, `/confidence`)
matched the Phase 4 mapping. Live calls were authorized for
`fixtures/py_repo` and the public ContextBench repositories, with full
capsules, under 6,000 requests and 20M input tokens. Used: 5,148 requests
(295 fixture, 4,853 ContextBench) and 13.2M input tokens, about $0.56.

- 5,148 of 5,148 responses were schema-valid and named `jev-1.13.0`. No
  HTTP error, 429 or timeout occurred.
- Observed difference from the docs' shape: score and probabilities are
  rounded to two decimals, so the sum and score/mean checks use a 0.02
  tolerance (one captured live answer is a unit test).
- Repeatability: of 100 dev capsules sent 3 times each, 13 were
  bit-identical, 97 stayed within 0.05 in value (mean spread 0.015, max
  0.085), and confidence moved by up to 0.22. JEV is nondeterministic but
  close; recorded answers replay deterministically.
- Latency per request: p50 306 ms, p95 409 ms, max 1.5 s.
- **Latency per context request** (measured on the 52 live runs' first
  budget, which made the calls): p50 21.1 s, p95 24.9 s, max 25.6 s with
  JEV, against 0.33 s / 0.48 s offline with the heuristic, about 64× slower.
  About 70 capsules are judged one request at a time. The preregistered
  cost gate reads per-request p95 and does not cover this. It is reported
  here, not gated, so a gate PASS does not mean JEV is fast enough for
  interactive use.
- Order sensitivity: one request per capsule, so batch order cannot
  matter; list order inside a capsule is canonical.

The fixture's 295 exchanges are committed (`fixture/jev/`, OXIDE's own
source) and replay in `cargo test` with no network. The ContextBench
exchanges are machine-local. An offline replay of all 52 tasks in a
fresh process reproduces the live run exactly (records, decisions,
routing and every metric except latency); the published metrics come
from that replay.

## Results (measured)

Configs: A = constant 0.5; B = heuristic; C = JEV for candidates and
neighbors (branch questions unsupported, so B's routing and capsules are
judged); paired on snapshot, query, routed universe, capsules, budget and
labels. Mean within-task AUROC over tasks with both classes; 95% intervals
are task bootstraps.

### Candidate judgment

| Split | A | B | C | C − B [95% CI] |
| --- | --- | --- | --- | --- |
| fixture (7) | 0.500 | 0.961 | 0.908 | −0.054 [−0.159, 0.028] |
| dev (11 with both classes) | 0.500 | 0.746 | 0.948 | +0.202 [0.087, 0.328] |
| calibration (12) | 0.500 | 0.653 | 0.916 | +0.263 [0.200, 0.339] |
| test (19) | 0.500 | 0.693 | 0.906 | +0.213 [0.122, 0.323] |

Pooled (calibration: 858 labeled, 57 relevant; test: 1,475, 96): AUROC
B 0.662 / 0.604, C 0.908 / 0.905. Precision/recall at the 0.4 floor on
test: B 0.074/0.844, C 0.200/0.844.
The heuristic ranks a long problem statement's lexical hits. JEV reads the
capsule and wins clearly on real issues, but not on the fixture's short,
name-like queries.

### Context quality (Phase 4 definitions)

| Split @ budget | Recall B → C | Gold token share B → C | File recall B → C |
| --- | --- | --- | --- |
| dev @256 | 0.012 → 0.024 | 0.101 → 0.238 | 0.526 → 0.684 |
| dev @1,024 | 0.200 → 0.217 | 0.197 → 0.244 | 0.557 → 0.694 |
| dev @4,096 | 0.312 → 0.591 | 0.169 → 0.406 | 0.600 → 0.744 |
| calibration @256 | 0.000 → 0.045 | 0.235 → 0.523 | 0.246 → 0.514 |
| calibration @1,024 | 0.044 → 0.189 | 0.237 → 0.499 | 0.396 → 0.596 |
| calibration @4,096 | 0.193 → 0.373 | 0.215 → 0.459 | 0.568 → 0.645 |
| test @256 | 0.048 → 0.082 | 0.268 → 0.421 | 0.367 → 0.749 |
| test @1,024 | 0.114 → 0.188 | 0.276 → 0.394 | 0.543 → 0.760 |
| test @4,096 | 0.251 → 0.405 | 0.170 → 0.382 | 0.623 → 0.806 |

Recall is fully packed required-symbol recall, not header-only coverage.
A equals B almost everywhere: on these queries both follow lexical order.
Expansion and the no-routing ablation barely move B. Routing coverage is the
ceiling: only 36–40% of verified gold is routed at all (Phase 3 routing,
unchanged), so 60–64% of gold is unobserved by any judge.

### Branch judgment (sampled, 8 regions per task)

B values every branch 0.5, so it never prunes (unnecessary exploration
1.0, pruning loss 0). On calibration, JEV at the router's 0.25 threshold
would cut unnecessary exploration to 0.56 at a pruning loss of 0.125 (1 of
8 useful regions); on test, to 0.375 with no loss (15 useful of 191). The
samples are too small for AUROC. Routing stayed
heuristic in every config; branch judgments were evaluated offline only.

### Calibration

**CALIBRATED** (offline statistic only). The calibration split has 858
answered points (687 correct, 171 incorrect); JEV's raw confidence ECE
is 0.176 there and 0.195 on test, so its confidence is overconfident as
reported. The 10-bin map fitted on the calibration split brings test ECE
to 0.040 (≤ 0.05; Brier 0.189 → 0.127). Accuracy at 25% coverage on test:
0.97 raw, 0.96 binned; AURC 0.078 → 0.076. Nothing in the runtime
changed: no map is applied, `DecisionPolicy::default()` keeps
`confidence_calibrated: false` and no confidence floor is set.

### Test split and gates

Unsealed once, offline (replay), after every configuration and the
calibration map were frozen; no threshold was chosen on it.

| Gate (amended preregistration) | Measured on test | Verdict |
| --- | --- | --- |
| G1 validity | 100% schema-valid, only `jev-1.13.0`, 97 of 100 repeats within 0.05 | PASS |
| G2 candidate quality | AUROC C − B +0.213 [0.122, 0.323], 19 tasks | PASS |
| G3 context at 1,024 | recall C − B +0.075 [0.026, 0.131] (gold share +0.119 [0.009, 0.240], not gated) | PASS |
| G4 cost | p95 409 ms per request, $0.0075 per context request | PASS |

Under the preregistration JEV candidate judgment is **promotable**. It
was **not promoted**: the heuristic stays the default, as the protocol
requires a separate decision. Against promotion as measured: about 21 s
per context request with sequential calls (G4 reads per-request latency
only), source disclosure to a hosted service, and 60% of gold never
reaching any judge. The gates were loosened (Amendment 1) before any
ContextBench result existed. The test split passes the original G2
(≥ +0.05) too. Its original G3 gold-share clause (lower bound > 0) also
holds, at +0.009.

## Deterministic fallback

`phase5_decisionbench.rs` drives `build_context` with JEV disabled,
unavailable, timing out, malformed, wrong schema (model, answer type),
over its request or token cap, the shared allowance exhausted, a
mismatched capsule digest, a duplicate or missing subject, abstention,
low calibrated confidence and uncalibrated confidence. Each case falls
back with its own visible reason, twice identically, to the offline plan
and byte-identical payload. Published knowledge is unchanged.

## Findings fixed during the phase

- The default 32 MiB LadybugDB buffer pool cannot publish a mid-size
  repository (ansible: 17k entities, 88k relations).
  `LadybugStore::with_buffer_pool` sets it; `new` keeps 32 MiB. The
  ContextBench run uses 1 GiB (peak RSS 0.5–3.1 GiB per task).
- The JEV adapter's lifetime caps counted answers a caching transport
  served. The first live run lost 24 tasks' judgments to the cap with no
  call made. Paid calls are now bounded at the tape that counts only live
  calls; that run's caches are kept aside, and the rerun answered every
  capsule.
- BM25 scores of the same snapshot differed in the last bits across
  processes: LadybugDB's bulk load and FTS build ran on 2 threads, so row
  order and the index statistics' summation order varied. Capsules render
  the raw score, so replay missed about 4 recorded answers per task.
  Publishing now runs single-threaded. Queries still run on 2 threads;
  their scores were identical across 16+ processes and 52 replayed tasks
  (measured, not guaranteed). Indexing cost, measured: 52 repositories
  in 437 s instead of 409 s (median 1.05×, worst 1.33×). Re-recording
  only the changed capsules took 405 live calls; the replay is now exact.
  The run before the fix (results kept machine-local) had dev C AUROC
  0.948 live vs 0.939 replayed.
- Review (independent, no blocker): the preregistration hash, test
  composition sealed in the manifest, decisions taken from the 1,024-unit
  run, branch comparison paired, sessions saved before each live phase,
  empty-gold tasks excluded from means, fixture refreeze only on request,
  billing before the late-answer check, `ureq` exact pin.

## Open (not Phase 5)

- Routing loses 60–64% of gold before any judge sees it: the largest
  measured loss, and a Phase 3 routing question.
- Larger, human-audited annotation, especially for "useful only in
  combination" and necessity.
- Live JEV at routing time (branch pruning) and its cost under deadlines.
- A batched or concurrent JEV transport (sequential calls add about 20 s per
  context request). Done in the
  [follow-up](phase-5-followup.md): bounded concurrent requests, offline
  p50 2.6 s, with the routing loss broken down by stage.
  [Follow-up 2](phase-5-followup-2.md): no routing load-order gain
  detected (underpowered dev gate), JEV drift measured from recordings.
  [Follow-up 3](phase-5-followup-3.md): 96/128-region budgets route more
  gold, but the heuristic never selects it; 64 kept.
  [Follow-up 4](phase-5-followup-4.md): four interpretable rescorings of
  the same candidates gain nothing at 1,024 units; the heuristic is kept.
- Downstream agent success is not measured.
