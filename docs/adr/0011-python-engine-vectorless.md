# ADR-0011: Python engine, one process, vectorless retrieval

## Status

**Accepted** — 2026-10-15, by the maintainer's explicit migration instruction on
`rewrite/v2`. This is a migration of the v2 implementation, not a new architectural
generation: the name stays OXIDE v2, and so do the mission, the domain model and
the structural principles.

Supersedes [ADR-0003](0003-kernel-runtime-control-plane-boundary.md) entirely and
the implementation-language, control-plane and vector/embedding parts of
[ADR-0001](0001-oxide-v2-rewrite.md). Amends the build and extension sections of
[ADR-0002](0002-ladybugdb-knowledge-store.md); LadybugDB stays. Leaves
[ADR-0010](0010-domain-identity-and-source-capture.md) standing (§ 7). Those
records keep their text; each carries a dated pointer to this one.

The last commit of the Rust/Bun implementation is `f79808a` ("openRepository
through the persistent runtime"). Everything this ADR removes is recoverable
from there.

## Context

Between 2026-10-07 and 2026-10-12, `rewrite/v2` built Phases 0–5 as a Rust
kernel (no I/O, no dependencies), a Rust runtime (LadybugDB adapter, capture,
sessions, JEV and embedding clients, DecisionBench) and a Bun/TypeScript control
plane. They were joined by a versioned NDJSON protocol over a supervised child
process. That was about 23,000 lines of Rust plus the TS control plane. What
the record shows:

- **Quality came from structure and lexical evidence, not models.** Phase 3's
  frozen baseline reached entry and routed coverage 1.0 on the fixture with
  lexical and structural channels only; no semantic channel ever existed.
  Every model-side experiment since was rejected or inconclusive: LFM2.5 as a
  semantic channel, and its routing/judge/PPR retry (`docs/semantic-eval`);
  EmbeddingGemma 2 (paused); field-aware lexical variants (lexical R&D
  closed); JEV validity (inconclusive, `docs/phase-5*.md`); agent-guided
  navigation (inconclusive, `docs/agent-navigation-eval`). The pre-v2
  evidence agreed: rerankers, Laya and the Julia judge were all rejected (SPEC §
  Historical evidence).
- **The two-language seam was expensive and slow to pay off.** Three
  commits (`c61e85f`, `32159d9`, `f79808a`) built the public TS API, the
  persistent runtime with its framing, bounds, supervision and capabilities,
  and finally `openRepository` across it. `indexRepository`, `search` and
  `buildContext` were still "contract only" in the public API
  (`docs/public-api.md` at `f79808a`). Every operation needed matching Rust
  and TS types, shared contract cases, a protocol version and supervision
  semantics before any user could call it.
- **The heavy work was already native code.** Parsing is tree-sitter, BM25 and
  graph storage are LadybugDB, and hashing is SHA-256. The Rust kernel's own
  work per request (bounded BFS over at most 64 regions, greedy selection over
  at most 64 candidates) is small, bounded policy.

The maintainer's priorities changed: a vectorless architecture (graph, metadata,
lexical retrieval, structural navigation); no embedding models, vector indexes
or local inference runtime; decision models optional and replaceable; and iteration
speed and maintainability over peak low-level performance.

## Decision

1. **One Python package, one process.** OXIDE v2 is the Python 3.12+ package
   `oxide` (`src/oxide/`), managed with uv, checked by pytest, Ruff and mypy
   (strict), with `mise run verify` as the one entrypoint. Its layers are the
   public API (`api.py`, `models.py`), pure policy (`core/`: Python
   extraction, retrieval, routing, packing), capture (`capture.py`) and storage
   (`storage/ladybug.py`), plus a thin CLI (`cli.py`). There is no Rust, no
   TypeScript, no IPC and no process supervision.
2. **The public Python API is the product boundary**: `index_repository`
   → `search` → `build_context` and typed results (frozen dataclasses). The CLI
   and future MCP and editor integrations are Python clients of that API. They
   present results and do not contain repository intelligence.
3. **LadybugDB stays**, through its Python wheel (`ladybug==0.21.2`, the same
   release and on-disk storage format 47 as the Rust pin). ADR-0002's rules
   carry over unchanged in substance: OXIDE's own exclusive lock, one database
   per generation, an atomic `CURRENT` pointer, `COPY` bulk load with exact
   read-back, read-only sealed generations, and the pinned FTS extension loaded
   by path after a digest check (never `INSTALL`). § Storage verification
   below records the Python-specific facts.
4. **Vectorless.** No embedding providers, vector or ANN indexes, semantic
   channel, local inference runtime or model-runner discovery. Retrieval is
   BM25 over OXIDE terms plus structural seeds from typed hints. Context comes
   from TreeRouter and deterministic selection. § 5 gives the reasons.
5. **Decision models stay optional and outside the engine.** The first Python
   slice has no `DecisionProvider` and no JEV adapter. The heuristic value
   (`core/packing.py: value`) is the single, named place a judge's validated
   estimate would replace. Models judge and deterministic code decides
   (thresholds, bounds, inclusion, budget). Bringing a judge back is a scoped
   follow-up behind that seam, not part of the engine's dependencies.
6. **Libraries do the recursive and latency-sensitive work.** Fact extraction
   is one compiled tree-sitter query; Python only maps captured nodes to
   scopes with a bisect and never walks syntax trees recursively. Adjacency is
   read per router layer, two queries per layer instead of one or two per
   region, and BM25 ordering, joins and range lookups run inside LadybugDB.
   Python holds only bounded policy over small sets.

## 1. Why Python

The engine's costs are in native libraries either way, and the policy layer
is small, bounded and changes often. Python gives one language from parser
facts to the public API, a REPL over real repository state, and the
evaluation ecosystem the project already uses (every harness under `docs/`
and `eval-agent/` is Python). Removing the Rust/TS seam deletes a class of
work (contract duplication, wire versioning, supervision) without removing a
measured capability.

## 2. Assumptions that changed

| Original v2 assumption (ADR-0001/0003, SPEC) | Now |
| --- | --- |
| A Rust kernel is needed for correctness boundaries and speed | Boundaries are module and test boundaries. Speed was measured (§ 4): the hot paths are native libraries, and per-request policy is bounded |
| TypeScript owns MCP, CLI and editor integrations | Python owns the API, CLI and future MCP, as clients of one engine |
| A long-lived supervised runtime must own the store, sessions and caches | A `Repository` object owns them in-process, with OXIDE's lock preventing a second owner |
| Semantic vectors are an optional but planned retrieval channel, with Ollama/llama.cpp runners | Removed (§ 5) |
| JEV and learned judges get first-class runtime clients | Optional and external; no provider infrastructure in the engine |
| The kernel must be dependency-free and I/O-free, enforced by compilation | `oxide.core` is pure policy over typed data plus the store view it is given; capture and storage own I/O |

## 3. Why the two-language runtime boundary is no longer justified

The boundary existed so that TypeScript integrations could not reimplement
repository intelligence, and so that one process could own long-lived
resources (ADR-0003). With integrations written in the same language and
calling the same API, the first reason becomes a review rule: integrations
call `oxide.api` and nothing below it. The second is met by an in-process
owner. What the boundary cost was the protocol (versioned envelopes, closed
error codes, contract cases in two languages), framing limits, a supervisor
with no-retry and kill semantics, cancellation gaps, two toolchains (rustup
plus cargo, and Bun), a native `-rdynamic` build for extension loading, and
an open multi-client ownership design. It delivered one repository operation
in three commits. None of those costs bought a measured quality or latency
benefit.

## 4. Accepted performance and packaging tradeoffs

Measured 2026-10-15, Linux x86_64, CPython 3.13, `CPUQuota=200%`,
`MemoryMax=3G`. The Rust figures are its Phase 3 records (release build);
the corpora differ where noted, so compare per entity, not per directory name.

| Measure | Rust (`docs/phase-3.md`) | Python (this ADR) |
| --- | --- | --- |
| `fixtures/py_repo` rebuild | 0.48–0.56 s | 0.27 s |
| asyncio rebuild | 0.86–0.95 s (1,283 entities / 5,224 relations, with `.pyc`) | 0.66 s (1,179 / 5,139, no `.pyc`) |
| email rebuild | 0.65–0.80 s (814 / 3,702) | 0.48 s (727 / 3,615) |
| CPython 3.14 stdlib (713 files, 18 MiB) | not measured | 6.3–6.7 s (20,123 / 94,693); parsing ≈ 2.7 s of it |
| Re-index with no change | republish skipped | 0.04 s (stdlib), 0.009 s (fixture) |
| Reopen | 21–25 ms | 13–20 ms |
| Lexical retrieval, warm | 5.7 ms (asyncio) | 6.4 ms (asyncio), 24 ms (stdlib) |
| Route (Rust) / whole `build_context` (Python), warm | route alone 263–295 ms (asyncio, 64 regions) | 83 ms (asyncio, 64 regions), 148 ms (stdlib) |
| Peak RSS | 149–160 MiB (asyncio) | 142 MiB (asyncio), 259 MiB (stdlib) |
| `import oxide` | n/a | ≈ 170–200 ms |

Accepted:

- Python-side parsing work (capture-to-facts) grows with source size: about
  2.7 s for the 18 MiB stdlib. Indexing is a full rebuild per change; there
  is no incremental indexing yet. If this blocks real repositories, the
  upgrade path, measured first, is parallel parsing (a process pool), then
  incremental derivation, before considering compiled helpers.
- Packaging: `ladybug` ships wheels for CPython 3.10–3.14 on manylinux,
  musllinux, macOS and Windows; tree-sitter ships abi3 wheels. OXIDE therefore
  requires `>=3.12,<3.15`. The FTS extension is pinned for Linux x86_64 only.
  On other platforms the lexical channel reports `unavailable` and structural
  routing still works. Atomic publication is verified on Linux only, as before.
- `Repository` is single-threaded, and a store has one owner across processes.
  There is no shared service or multi-client attach.
- The CLI and API start with an import of about 0.2 s, which is fine for a
  library and a CLI. A daemon is not planned.

## 5. Why vector retrieval is removed

- **No measured gain.** v2 never had a semantic channel. Every embedding or
  learned-judge experiment that could have justified one was rejected or
  inconclusive (§ Context), and on the fixture the lexical and structural
  baseline already covered all gold entry points.
- **Complexity it would force.** Runner discovery and identity, embedding-space
  fingerprints, invalidation, model downloads, fallback states and ANN recall
  controls make up a large share of SPEC, none of it exercised.
- **Product fit.** OXIDE's claim is structural context. Lexical entry points
  plus TreeRouter keep the offline path free of models and networks, as SPEC
  always required.

Reintroducing vectors needs a new ADR with paired evidence against this
baseline. It is not a deferred feature.

## 6. Invariants that remain essential

- Public API as the stable boundary; results are typed values that name their
  snapshot.
- Repository, snapshot and derivation identity (ADR-0010): deterministic,
  snapshot-local structural keys; content-addressed `SnapshotId` and
  `DerivationId`; a random persisted `RepoId`; byte-exact paths; ordinals for
  duplicates.
- Typed source locations (half-open byte ranges with the file digest) and
  provenance on every relation and context item. Hydration only from retained,
  digest-verified bytes, never the live worktree.
- TreeIndex projection 1 (physical containment) with typed cross-edges.
  Unresolved and ambiguous relations are explicit and never invented.
- TreeRouter: bounded BFS with visible bounds and root fallback, and no
  model-controlled traversal.
- Deterministic selection and packing: stable order, `used <= budget` in
  declared units, header reductions marked, overlaps merged once, every
  omission reasoned.
- Explicit diagnostics: coverage (complete, partial, unsupported), skipped
  entries, case collisions, hint issues, channel status and degradations.
- Persistent, atomic publication and fail-closed recovery: rebuilt, never
  migrated.
- Agent-agnostic output; local-first, with no network except the explicit
  `oxide prepare`.

## 7. Superseded and amended records

| ADR | Effect |
| --- | --- |
| ADR-0001 (Proposed) | Superseded in part: the Rust kernel/runtime and TS control plane bullets, "graph/vector store", the embedding-provider/runner boundary and JEV as a runtime adapter. Mission, product definition, structural principles and rebuildable state stand. Its status is otherwise unchanged |
| ADR-0002 (Accepted) | Amended: § 2 (Rust build from a pinned static archive) and the `-rdynamic` linking in § 3 are replaced by the Python wheel plus `oxide prepare` for the pinned FTS file. The vector accelerator is dropped. The physical schema is now `oxide-ladybug-py-v1` (one `Rel` table with a `kind` property, plus `Opaque` edges to `Unresolved` nodes). Lock, generation and publication rules stand |
| ADR-0003 (Proposed) | Superseded: no runtime process, transport or TS control plane remains |
| ADR-0010 (Accepted) | Stands. Keys are now text (`oxide-id-v2`: `file:<path>`, `symbol:<path>#A.b~1`, `module:python:a.b`, `repo`) under the same structural rules. The capture encoding is `oxide-capture-v3` (ADR-0010's inputs plus `.gitignore` digests, as `v2-gitignore` had) |

SPEC's candidate ADRs 0004–0009 (capsule, learned decisions, rebuildable
state, TreeIndex/TreeRouter, runner boundary, DecisionProvider/JEV) remain
unwritten. 0008 (runner/embedding boundary) is obsolete under § 5.

## Storage verification (Python, 2026-10-15)

Spike and test findings at `ladybug==0.21.2`:

- The wheel bundles the native library: no native preparation step, and
  `get_storage_version() == 47`.
- `COPY` from quoted CSV loads 50,000 nodes and 49,999 relationships in 0.93 s.
  Read-back counts are compared before publishing.
- Variable-length and `IN $list` queries work. Each router layer is one
  adjacency query per direction.
- FTS is not built in. The pinned extension file loads by path into both the
  writer and the read-only databases of one process.
- **A parameterized `QUERY_FTS_INDEX` crashes the process (SIGSEGV) the second
  time the binding runs its cached prepared statement.** Workaround: the query
  is a literal assembled only from OXIDE terms (`[a-z0-9 ]`, asserted), so it
  cannot be injected. Re-test at every pin bump.
- `$end` is a reserved word in parameters (parser error); renamed.
- A second process opened the store read-write while the first held it, and
  the binding did not refuse. OXIDE's `flock` remains the ownership mechanism,
  as ADR-0002 § 4 required.
- The extension host returns HTTP 403 to urllib's default User-Agent, so
  `oxide prepare` sends `oxide/<version>`.

## What was removed, retained, replaced

- **Replaced** (Rust/TS → Python): capture, the Python language slice,
  derivation and validation, the LadybugDB adapter and publication, lexical
  terms and retrieval, TreeRouter, selection and packing, and the public API.
  A CLI is added.
- **Retained as reference evidence** (read-only, not rebuilt): every report
  under `docs/`, the frozen Phase 3/4/5 artifacts, and the fixtures. The Phase 3
  baseline is a test oracle (`tests/python/test_baseline.py`). Retrieval and
  routing reproduce it exactly on all 7 tasks: entities, lexical ranks, BM25
  scores to 1e-12 relative, routed entities and depths. Payload bytes are not
  compared; the payload format is `oxide-payload-v2`.
- **Removed from production** (recoverable at `f79808a`): the Rust kernel and
  runtime crates, contract cases, LadybugDB spike crate, native preparation,
  Bun control plane, NDJSON transport and supervisor, embedding-runner seam,
  JEV adapter and HTTPS transport, DecisionBench tooling, capsules, the
  `DecisionProvider` contract, and bounded expansion (Phase 4 measured it adding
  nothing over depth-2 routing on the fixture).

## Reconsider if

- Indexing cost blocks real repositories after parallel parsing and
  incremental derivation are measured;
- the LadybugDB Python binding shows further crash-class defects, or a pin bump
  breaks the storage format in a patch release (ADR-0002's criteria);
- a concurrent multi-client service becomes a product requirement;
- paired evidence shows a semantic or learned channel beating the lexical and
  structural baseline on held-out repositories at acceptable cost.
