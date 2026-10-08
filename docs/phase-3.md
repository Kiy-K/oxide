# Phase 3 — entry-point retrieval, TreeRouter and the evaluation baseline

Status: **complete** (2026-10-08) on `rewrite/v2`. Phase 4 has not started.
Decisions stay inside SPEC, [ADR-0002](adr/0002-ladybugdb-knowledge-store.md)
and [ADR-0010](adr/0010-domain-identity-and-source-capture.md); no ADR status
changed. Linux x86_64 only, as in Phase 2B.

> Retrieval finds entry points; TreeRouter constructs the structural
> candidate universe. Neither selects or packs context.

## What exists

| Piece | Where | Tests |
| --- | --- | --- |
| OXIDE terms, lexical request/result, hit order | `kernel/src/lexical.rs` | unit tests there; shared store case `lexical_search_is_scoped_ordered_and_bounded` |
| `ReadView::lexical` / `ReadView::named` (port) | `kernel/src/store.rs`, `runtime/src/storage/` | shared store cases (fake and LadybugDB) |
| Entry-point retrieval → `CandidateSet` | `kernel/src/retrieve.rs` | `kernel/tests/retrieval_routing.rs` |
| TreeRouter baseline | `kernel/src/route.rs` | `kernel/tests/retrieval_routing.rs` |
| LadybugDB FTS (pinned extension, per-generation index) | `runtime/src/storage/mod.rs`, `native/prepare.sh`, `runtime/build.rs` | `runtime/tests/ladybug_contract.rs` |
| Embedding seam: deadlines, semantic channel state | `runtime/src/embedding.rs` | unit tests there (fake runner) |
| Frozen evaluation harness and artifacts | `runtime/tests/phase3_eval.rs`, `docs/phase-3/baseline-v1/` | the harness itself |
| Rebuild/read/route/memory measurement | `runtime/examples/rebuild_bench.rs` | measurement aid, not a test |

## Phase 2 carry-over

### Store encoding version

Every generation's `MANIFEST` now records `SCHEMA_VERSION` (2) beside the
LadybugDB storage format (47). On reopen both are checked: older (including
schema-1 manifests, which had no field) → the generation is discarded and
rebuilt, never read; newer → `Unsupported`, nothing deleted; malformed →
`Corrupt`. Tests: `older_adapter_encoding_is_discarded_for_rebuild`,
`newer_adapter_encoding_is_refused_and_kept` plus the existing storage-format
pair. Schema 2 drops the `Pending` table, adds the `terms` column and the FTS
index; the `storage-schema` derivation component is `oxide-ladybug-schema-v2`.

### Write path

Measured at the pin (6,500 nodes / 6,499 relations, one transaction):

| Strategy | Nodes | Relations |
| --- | --- | --- |
| prepare per row (Phase 2) | 1.24 s | 6.59 s |
| one prepared statement, executed per row | 0.54 s | 3.39 s |
| `UNWIND` of 1,000-row struct lists | 0.08 s | 3.83 s |
| `COPY FROM` CSV | 0.023 s | 0.046 s |

Keyed `MATCH … CREATE` stays slow with reuse or `UNWIND`; bulk load is the
fix. A staged generation now keeps its facts in memory (a write is checked
whole, then kept), and publication validates, writes CSVs into the staging
directory, `COPY`s them in one transaction, then reads back every entity and
relation and compares them exactly before commit (ADR-0002 § 6.3). CSV
fields are always quoted with doubled quotes. Commas, quotes, backslashes,
CR/LF, tabs, non-ASCII and empty strings were verified to round-trip
byte-exactly through `COPY` at the pin. Store paths containing `'` or `\`
are refused (the COPY path is a literal).

Rebuild into an empty store, release build, 2 threads, `CPUQuota=200%`
(write + publish in parentheses):

| Input | Entities / relations | Phase 2 | Phase 3 | Store on disk (P2 → P3) |
| --- | --- | --- | --- | --- |
| `fixtures/py_repo` | 65 / 194 | 0.68 s (0.20 + 0.42) | 0.48–0.56 s (0.000 + 0.44–0.51) | 5.2 → 8.6 MiB |
| CPython 3.14 `email/` | 814 / 3,702 | 8.7 s (Phase 2B record) | 0.65–0.80 s (0.004 + 0.54–0.67) | 15.5 → 20 MiB |
| CPython 3.14 `asyncio/` | 1,283 / 5,224 | 13.05 s (4.54 + 8.35) | 0.86–0.95 s (0.005 + 0.70–0.77) | 19 → 23 MiB |

Phase 3 publish includes the FTS index build (~0.3 s, about 5 MiB fixed plus
4 MiB on asyncio). Capture + derive is unchanged (0.12–0.14 s on asyncio).

### Generation lifetime

The store holds only the current generation strongly. Superseded generations
are kept as weak handles: readable (and re-openable, and re-activatable for a
reverted capture) while a view pins them, closed when the last view drops,
and their directory removed on the next store call. `MemoryStore` follows the
same rule, and the shared case `superseded_generations_are_freed_when_unpinned`
holds both to it. `RepositorySession::rebuild` re-points to a still-open
equal generation, otherwise rebuilds.

Same facts republished under 12 new keys in one process, a reader per cycle
(`rebuild_bench … 12` vs the same loop against `fa6d161`):

| | after 1 | after 4 | after 8 | after 12 | generation dirs |
| --- | --- | --- | --- | --- | --- |
| Phase 2 | 65 MiB | 164 MiB | 351 MiB | 457 MiB | 12 |
| Phase 3 | 180 MiB | 219 MiB | 233 MiB | 207 MiB | 1 |

Phase 3's first value includes the bench's earlier read/route phase and the
loaded FTS extension; over 30 cycles RSS stays within 185–219 MiB with one
generation directory. Phase 2 grew ~35 MiB per rebuild.

## Entry-point retrieval

`retrieve(view, query, context, semantic, config) -> CandidateSet`.

- **Lexical channel**: LadybugDB FTS (pinned 0.21.0 extension, SHA-256
  checked by `native:prepare` and again by the runtime before `LOAD
  EXTENSION` by path; never `INSTALL`). The index is over an OXIDE-owned
  `terms` column: `lexical::document` = terms of name, signature and file
  path; `lexical::terms` splits at non-alphanumerics, camelCase, acronym
  ends and letter/digit changes, lowercases, and adds the joined identifier
  (`RetryPolicy` → `retry policy retrypolicy`), dropping one-character
  terms (the pinned FTS never matches them; a name like `q` is reachable only
  through a symbol hint). Query text is split the same way. FTS adds porter
  stemming and default stopwords, which the fake store does not: part of its
  scorer, not of the contract. Scorer
  `ladybug-fts-0.21.0-bm25;k1=1.2;b=0.75;stemmer=porter;disjunctive`.
- **Ordering**: measured at the pin, FTS returns matches unordered and
  `top := k` cuts *between equal scores* arbitrarily, so the adapter fetches
  every match and `lexical::rank` orders by score then `EntityId`, then
  applies the limit. Scores are positive and finite or the store is
  `Corrupt`. The fake store scores by distinct-term overlap
  (`memory-term-overlap-v1`): the contract fixes order, scope, bounds and
  score meaning per scorer, not equal numbers across scorers.
- **Index identity**: `lexical` is a derivation component
  (`ladybug-fts-0.21.0;stemmer=porter;stopwords=default;oxide-terms-v1`, or
  `unavailable` when the extension is missing or its digest is wrong), so a
  generation built without an index has a different `DerivationId`. A view
  without the capability returns `Unsupported`, and retrieval reports the
  lexical channel `Unavailable(reason)`; structural seeds still work.
- **Structural seeds** (`QueryContext`, in this order): selection → the
  innermost declaration containing the byte range (else the file); paths →
  exact, else a unique case-folded match flagged `case_insensitive`, else
  `Ambiguous`/`NotFound`; symbol hints → exact name, or a dotted suffix of the
  declaration path (`AuthService.refresh_token`), at most `symbol_limit`
  (truncation reported); changed paths. Every seed keeps its kind and hint;
  every unmatched hint is a `HintDiagnostic`.
- **Merge**: entries are deduplicated by `EntityId` with all evidence kept.
  Order is a declared precedence (seeds in hint order, then lexical rank),
  not a fused score. Channel scores keep their own names and scales;
  semantic is reported (`Unconfigured`/`Unavailable`), never scored.
- **Frozen baseline** `retrieve::BASELINE`: lexical and structural on,
  `lexical_limit` 20, `symbol_limit` 8, `oxide-retrieval-v1`. Historical
  RRF K=60 / 0.6–0.4 is not implemented: with no semantic channel it reduces
  to lexical order. It stays a comparison configuration for when vectors
  exist, not a default.

## TreeRouter baseline (`oxide-router-bfs-v1`)

Level-synchronous BFS over TreeIndex projection 1. Layer 0: entry points in
CandidateSet order, or the root region when there are none (root fallback;
projection 1 has no directory scopes, so scope fallback is the hint-matched
file seeds). An explored region expands to physical children, its containing
scope (never the repository root), then both ends of the followed cross-edge
kinds (`Calls`, `References`, `Imports`, `Implements`, `TestedBy`, logical
`Contains`), resolved and ambiguous targets, each in domain order,
deduplicated. A visited set terminates cycles (counted as revisits).

Limits (`BASELINE_LIMITS`): 64 regions loaded, depth 2, 16 neighbors per
region, 2,048 edges examined, 64 judgments. Each fires visibly:
`Deferred(Regions|Edges|Depth)` steps, `Fanout` truncations per region,
`stopped_by`. Judgments: with a provider, each layer's non-entry regions are
judged in one batch (`BranchValue` capsules) within the route cap and the
shared `Allowance`. Rust prunes only a validated value below 0.25; any
fallback (missing, invalid, abstained, low confidence, unsupported, over
allowance, provider failure) keeps the route unchanged. Entry points are
never judged or pruned. With no provider nothing is requested; the eval
asserts the Phase 1 heuristic produces the identical route. Candidates are
returned in domain order with depth, every origin (entry point, root
fallback, region member, scope, cross-edge neighbor with relation and
direction), and entry evidence only for entry points. Routing is not
inclusion.

Read path: navigation reads one adjacency per region (the parent is the one
the ID implies, a publication invariant), and the adapter answers adjacency
with one unordered query per direction across all relation tables, filtering
and ordering by the stored domain ordinal itself: at the pin native
`ORDER BY` cost ~2 ms of a ~3.2 ms adjacency query.

## Evaluation baseline (frozen: `docs/phase-3/baseline-v1/`)

`manifest.json` (snapshot/derivation, configs, scorer, limits, gold mapping
rule, metric definitions), `results.json` (every task × config: the
CandidateSet with ranks/scores/seeds, routed candidates with depth/origin,
every route step and truncation), `metrics.json` (macro means). The harness
(`phase3_eval.rs`, part of `cargo test`) fails unless a run reproduces all
three byte for byte; a changed configuration is a new baseline directory.
It also asserts fake/real routing parity and heuristic-equals-no-provider on
every run.

Corpus: the 7 Python tasks of the retained fixtures (5 from
`fixtures/benchmark.json`, 2 from `fixtures/structural_benchmark.json`, whose
`anchor_symbol` becomes a symbol hint), 9 gold symbols, all mapped
(`file#A.b` → ordinal-0 `SymbolId`; none missing or ambiguous). TypeScript
tasks have no v2 language slice. **A regression floor on a 65-entity
fixture, not evidence of generalization.** Context quality and downstream
agent success are not measured or claimed.

### Retrieval (entry points)

| Config | R@1 | R@5 | R@10 | R@20 | MRR | Entry coverage | Entries |
| --- | --- | --- | --- | --- | --- | --- | --- |
| lexical-only | 0.286 | 0.857 | 1.0 | 1.0 | 0.540 | 1.0 | 15.7 |
| structural-only | 0 | 0 | 0 | 0 | 0 | 0 | 0.3 |
| lexical + structural | 0.286 | 0.857 | 1.0 | 1.0 | 0.516 | 1.0 | 15.7 |

All gold entry points came from the lexical channel. The structural hints
name the anchor (`Notifier`, `should_retry`), not the gold implementors and
callers, so seeds cover no gold directly, and seeding first costs MRR on
`py-callers-should-retry` (rank 2 → 3). Lowest gold ranks: 9 and 10
(`py-semantic-http-retries`).

### Routing (from those entry points)

| Entry config | Entity coverage | Region (file) coverage | Pruning loss | Deferral loss | Unreached | Candidates | Regions | Edges examined | Stopped by |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| lexical-only | 1.0 | 1.0 | 0 | 0 | 0 | 33.3 | 33.3 | 162.4 | depth |
| structural-only | 0.429 | 1.0 | 0 | 0.571 | 0 | 26.9 | 26.9 | 147.9 | depth |
| lexical + structural | 1.0 | 1.0 | 0 | 0 | 0 | 33.3 | 33.3 | 162.4 | depth |

Max depth reached 2, max fanout seen 15–16, no judgments (no provider).
Structural-only is root fallback on the 5 hint-free tasks; from the
`Notifier` hint it routes both implementors (via incoming `References`); from
`should_retry` it cannot reach the caller, whose call is an attribute call
the Python slice leaves unresolved (no type inference). That is a measured
structural limit, not a routing bug.

**No-routing diagnostic ablation**: plain lexical top-N with N = the routed
universe size (33.3 on average) also covers all gold (1.0). On this fixture
routing adds no measurable coverage over a same-size lexical list; the
fixture is too small to separate them (N is half the repository).

## Performance (release, Linux x86_64, 2 threads, `CPUQuota=200%`)

Cold = first call after reopen; warm = median of 20. Two runs each.

| | `py_repo` | `email/` | `asyncio/` |
| --- | --- | --- | --- |
| reopen (store + current generation + FTS load) | 24–25 ms | 19–24 ms | 21–25 ms |
| `entities` (1 id) cold / warm | 5 / 1.0 ms | 2.5–3.7 / 0.7–1.2 ms | 3.5–4.3 / 1.0–1.2 ms |
| `region` (file) cold / warm | 17–18 / 5.5–8.1 ms | 11–13 / 4.6–6.3 ms | 17–18 / 4.4–6.3 ms |
| `retrieve` cold / warm | 7.6–15 / 1.9–3.0 ms | 9.8–12 / 1.9–2.5 ms | 11–14 / 5.7–5.8 ms |
| `route` (baseline) cold / warm | 166–240 / 172–235 ms (32 regions) | 160–219 / 146–163 ms (37) | 302–305 / 263–295 ms (64) |
| RSS after reads | 124–130 MiB | 134–144 MiB | 149–160 MiB |

Before the adjacency change, an asyncio route of 64 regions took 3.3 s cold
/ 3.9 s warm (~55 ms per region) and a file region 47 ms warm. About 4.5 ms
per region remains, mostly fixed per-query cost (~0.25 ms minimum,
~1.2 ms for an adjacency query). The query text is in `rebuild_bench.rs`.

## Embedding / model-runner seam

No HTTP client to Ollama or llama.cpp was added: no HTTP dependency exists,
and a real adapter would broaden the phase (allowed stop point). The typed
seam gained deadlines (`probe`/`embed` receive the time left; `embed_all`
never starts a batch past its deadline and returns `Timeout`) and
`channel_state`, which maps semantic status into the CandidateSet:
unconfigured → `Unconfigured`; configured but down, mismatched or unverified
→ `Unavailable(reason)`; even a ready runner is `Unavailable` (no vector
index yet). Configured identity, distinct unconfigured/unavailable/
discovered states and no automatic switching are the Phase 1 tests.

## Review

An independent review (Sonnet 5.5) found no blocker or high issue. Its four
medium findings were fixed, each with a test: one-character terms diverged
between stores (now dropped by `lexical::terms`); `Candidate.origins`
depended on visit order (now every way a region was reached, collected for
all seen regions); a truncated name lookup was reported as `NotFound`/seed
truncation (now `Truncated` at the lookup bound); a failed `begin` or a
leftover generation directory could wedge a key until restart (staging is
cleaned on failure, `begin` checks only live generations, and publication
clears a stale sealed directory). Also fixed: newer-anywhere is refused
before older-anywhere is discarded; the FTS digest is re-checked on every
use; `activate` poisons the store only from the CURRENT rename on.
Remaining low findings are listed below.

## Open (not Phase 3)

- `max_edges` counts edges after each region's fanout cut and is checked
  before each load (may overshoot by two fanouts); store work per region is
  bounded only by `MAX_REQUEST_ITEMS`, and the adapter reads an anchor's full
  degree.
- The region view cuts cross-edges before the router's follow filter, so
  unfollowed kinds can use fanout slots (small for Python today).
- No per-field size cap: one multi-megabyte signature fails publication
  with a buffer-pool error (measured: 3 MB fails, 300 KB works at 32 MiB).

- Real Ollama/llama.cpp request adapters with fake-server fixtures, a vector
  index, semantic-only / exact-vector / RRF controls.
- Retrieval/routing service operations (the v1 protocol is unchanged).
- Pushing a bounded top-k into FTS and adjacency once tie handling and cost
  are measured; per-query fixed cost dominates routing.
- A larger mapped corpus to separate routing from same-size lexical lists.
