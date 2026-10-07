# ADR-0001: Rebuild OXIDE as a graph-native code context engine

## Status

**Proposed** — 2026-10-07. This records the top-level rewrite proposal. It is not an implementation change or a SemVer release decision.

## Context

OXIDE transforms repository structure into task-specific context. Given repository state, a query, and a budget, it should select a small, coherent set of evidence for a downstream developer or coding agent. Retrieval produces candidates; selection produces context.

The prototype supplies valuable fixtures, regression tests, ContextBench and agent harnesses, embedding correctness lessons, indexing measurements, frozen retrieval baselines, and failed experiments. Its SQLite schemas, IDs, migration history, module layout, and incidental public APIs reflect that prototype's evolution. They are not requirements for a clean architecture.

Historical results do not establish that merely adding structure or a model improves context. Reranking, structural selection features, and allocation experiments failed quality/cost gates in documented settings. Rewriting must retain that evidence and make stages measurable rather than presume a graph or learned judge guarantees better outcomes.

“OXIDE v2” identifies an architectural generation. The prototype has not reached v0.2; that fact does not constrain the rewrite's timing or design.

## Decision

Propose a clean rewrite on a separate branch, tentatively `rewrite/v2`, as a graph-native code context engine with:

- A **Rust kernel** for repository domain logic, parsing/indexing orchestration, retrieval, structural operations, candidate graphs/capsules, deterministic selection, expansion, budgeting, and packing.
- A **Rust runtime** for repository sessions, I/O, database connections, embedding/decision-model resources, caches, concurrency, scheduling, telemetry, and a typed service boundary.
- A **TypeScript control plane** for MCP, editor/agent integrations, SDKs, configuration/application UX, workspace orchestration, process lifecycle, and remote adapters.
- **LadybugDB as the initial graph/vector store**, isolated behind a domain-facing `KnowledgeStore` abstraction.

The existing implementation remains a reference and benchmark source, not the v2 code foundation. Start as a modular monolith with clear responsibility boundaries; split crates/packages when a compilation, reuse, runtime, or API boundary justifies it.

Learned components are optional judgments over bounded evidence. Deterministic Rust owns policy and fallback. Repository knowledge is derived state rebuildable from source; legacy SQLite migration compatibility is not required.

The architectural source of truth is [SPEC.md](../spec/SPEC.md). [BOOTSTRAP.md](../BOOTSTRAP.md) gives future implementation phases and exit gates. This ADR does not freeze detailed schemas, transports, IDs, capsule fields, model heads, fusion, or selection algorithms.

## Decision drivers

1. Code relationships and structural evidence need explicit domain identity and provenance.
2. Candidate recall, final context quality, and downstream success need separate optimization and evaluation.
3. Correctness-critical policy must be testable without models, integrations, or a database.
4. Expensive resources and I/O need consistent lifecycle and repository-session ownership.
5. TS integrations must consume one engine instead of implementing repository intelligence again.
6. Models, storage, and retrieval experiments must remain replaceable, benchmarked, and comparable to simple baselines.
7. Future decision datasets should arise from runtime capsule structures with matching distributions.
8. A solo-maintainable architecture should avoid compatibility burden and unnecessary package proliferation.

## Architecture overview

```text
TS control plane → typed Rust runtime service → Rust kernel
                                                ↓ domain port
                                          KnowledgeStore
                                                ↑ implemented by
                                  runtime-owned LadybugDB adapter
```

The kernel defines the storage/domain contracts. It does not depend on LadybugDB native values, IDs, query results, or Cypher. Runtime owns the adapter and connection resources. TS never accesses the repository store directly.

The pipeline is retrieval → CandidateSet → CandidateGraph → CandidateCapsules → heuristic/optional learned judgments → deterministic selection → structural expansion → budget packing → ContextBundle.

Official LadybugDB docs inspected on 2026-10-07 document embedded property graphs, a Rust `lbug` API, vector and FTS extensions, and one read/write database owner with connections from that owner. Rust extension linking, version/target packaging, index mutation, persistence, and concurrency behavior still require an OXIDE adapter spike. The capability observations and validation gates are recorded in SPEC.md; no database spike was run in this session.

## Consequences

### Positive consequences

- Product/domain structure drives the architecture rather than old storage and API accidents.
- Retrieval, judgments, selection, expansion, and packing have inspectable artifacts and isolated tests.
- Offline deterministic behavior remains useful even if a model fails or never earns promotion.
- A narrow capsule seam aligns inference, diagnostics, and future DecisionBench data.
- Graph storage can change without rewriting domain identities or exposing native database objects to consumers.
- Integrations share consistent Rust behavior; the runtime can retain expensive resources across requests.
- No old DB migrations or permanent compatibility obligations are imported by default.

### Negative consequences / risks

- A clean rewrite incurs implementation cost and may temporarily offer fewer languages/integrations than the prototype.
- Graph-native storage does not itself establish better retrieval, selection, or downstream success.
- LadybugDB introduces native build/packaging, extension, persistence, and concurrency validation work; the initial choice may need reconsideration.
- Typed service and capsule contracts require versioning and discipline across Rust, TS, and datasets.
- Fine-grained resolved relationships can be expensive or ambiguous across languages; provenance and partial coverage must be visible.
- Domain-specific labels and repo-held-out evaluation remain major work before a learned decision model can ship.
- Dropping accidental compatibility needs an explicit user transition story before release, even though old indexes are rebuildable.

## Rejected alternatives

| Alternative | Reason for rejection as the rewrite foundation |
| --- | --- |
| Incrementally reshape the prototype while preserving SQLite schema/layout/APIs | Carries constraints that the clean rewrite explicitly seeks to remove; maintenance can continue separately |
| Port retrieval, selection, or indexing into TS | Creates a second repository engine and weakens deterministic Rust policy ownership |
| Treat vector top-K or reranker output as final context | Conflates discovery with task-specific, structurally coherent budget selection |
| Mandatory coding/general-purpose LLM or autonomous selection agent | Exceeds the product mission and makes offline fallback/policy measurability harder |
| Expose LadybugDB/Cypher/native node IDs through kernel APIs | Makes an implementation choice the domain identity and prevents clean fake-store contracts |
| Immediately split into many tiny crates/services | Adds build/API/runtime overhead before there is evidence for those boundaries |

These decisions do not rule out a different future storage adapter, fusion baseline, or well-evaluated specialized judge. Such changes need evidence and a focused ADR.

## Migration / transition strategy

1. Keep main and legacy files intact during architectural documentation. Do not bootstrap product code in this session.
2. Before later implementation, establish the separate rewrite branch and resolve required foundational decisions from SPEC.md. Do not mark this Proposed ADR Accepted automatically.
3. Inventory and pin historical fixtures, regressions, scorer corrections, raw dumps/manifests, negative results, and available reproduction environments. Missing local artifacts are explicit gaps.
4. Build fresh domain contracts and a fake store before the real adapter. Port proven behavior/test intent selectively; each code reuse needs a spec-based justification.
5. Use an isolated new derived-store/cache namespace and rebuild from source. Do not read, migrate, overwrite, or delete the legacy SQLite index.
6. Establish v2 deterministic baselines and stage evaluations. Compare to the legacy binary through a test/evaluation adapter where useful, without preserving its architecture.
7. Advance bootstrap phases only after their exit gates. Optional learned inference follows working deterministic context construction.
8. Decide release naming, integration compatibility adapters, user upgrade UX, and branch merge/replacement separately. This proposal authorizes none of those publication actions.

## Validation criteria

- Kernel boundary tests/dependency checks exclude DB-native and integration types; TS consumers cannot implement selection or access storage directly.
- Fake and LadybugDB stores pass shared domain contracts, including scoped reads, bounded adjacency, identity, publication/recovery, and capability handling.
- Context construction works offline with no learned component and produces budget-valid, source-backed bundles with reasons.
- Stages are independently replayable and report retrieval quality, context quality, and downstream outcomes separately.
- Baselines and negative findings remain available with scorer/model/corpus/configuration provenance.
- Capsule runtime/dataset representation aligns; malformed or uncertain judgments invoke explicit deterministic fallback.
- Learned or structural challengers are promoted only against preregistered quality/cost gates; no improvement is claimed from architecture alone.

## Follow-up ADR candidates

These are proposed future records, not additional decisions embedded in ADR-0001:

- ADR-0002: LadybugDB as the derived repository knowledge store.
- ADR-0003: Rust kernel/runtime and TypeScript control-plane boundary.
- ADR-0004: CandidateCapsule as the learned-model boundary.
- ADR-0005: Learned decisions with deterministic Rust policy.
- ADR-0006: Rebuildable repository state and no legacy DB migration.

Identity, transport, token accounting, and a concrete TreeSelect algorithm may need separate ADRs when their alternatives are ready for review.
