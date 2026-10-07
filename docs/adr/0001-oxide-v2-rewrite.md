# ADR-0001: Rebuild OXIDE as a structural context engine for agents

## Status

**Proposed** — 2026-10-07. This records the top-level rewrite proposal. It is not an implementation change or a SemVer release decision.

## Context

OXIDE is an open-source context engine for agents. Its mission remains: **OXIDE transforms repository structure into task-specific context.** More specifically, OXIDE indexes repository knowledge into a structural TreeIndex and routes each task through that structure to construct a bounded, high-value ContextBundle for software agents. It remains agent-agnostic: agents consume its output; OXIDE is not itself an autonomous coding agent.

Structure precedes similarity. Lexical/vector retrieval finds useful entry points; TreeRouter explores worthwhile structural regions. Judges estimate relevance/value; deterministic Rust decides traversal and final inclusion; ContextPacker constructs the bounded output. TreeIndex and TreeRouter are foundational, not optional post-retrieval enhancements.

The prototype supplies valuable fixtures, regression tests, ContextBench and agent harnesses, embedding correctness lessons, indexing measurements, frozen retrieval baselines, and failed experiments. Its SQLite schemas, IDs, migration history, module layout, and incidental public APIs reflect that prototype's evolution. They are not requirements for a clean architecture.

Historical results do not establish that merely adding structure or a model improves context. Reranking, structural selection features, and allocation experiments failed quality/cost gates in documented settings. Rewriting must retain that evidence and make stages measurable rather than presume a graph or learned judge guarantees better outcomes.

“OXIDE v2” identifies an architectural generation. The prototype has not reached v0.2; that fact does not constrain the rewrite's timing or design.

## Decision

Propose a clean rewrite on the separate branch `rewrite/v2` as a structural, graph-native context engine for agents with:

- A **Rust kernel** for repository domain logic, parsing/indexing orchestration, foundational TreeIndex/TreeRouter, entry-point retrieval, structural operations, candidate graphs/capsules, deterministic inclusion/expansion/budget policy, and ContextPacker.
- A **Rust runtime** for repository sessions, I/O, database connections, embedding/decision provider clients, request batching, caches, concurrency, scheduling, telemetry, and a typed service boundary. External runners/services execute inference.
- A **TypeScript control plane** for MCP, editor/agent integrations, SDKs, configuration/application UX, workspace orchestration, process lifecycle, and remote adapters.
- **LadybugDB as the initial graph/vector store**, isolated behind a domain-facing `KnowledgeStore` abstraction.

The existing implementation remains a reference and benchmark source, not the v2 code foundation. Start as a modular monolith with clear responsibility boundaries; split crates/packages when a compilation, reuse, runtime, or API boundary justifies it.

DecisionProvider is designed from the beginning for a deterministic heuristic judge, first-class optional JEV, future local specialized judges, and optional stronger-judge escalation. Judges make narrow fuzzy candidate/branch/neighbor value and uncertainty estimates; they are not code-correctness reasoners. **Models judge; deterministic Rust decides.** Offline operation works without JEV, semantic embeddings, or any learned judge.

**OXIDE owns context engineering, not model serving.** It owns provider contracts, persisted provider/model identity, request orchestration/caching/fallback, and retrieval/routing/selection semantics. Preferred initial local embedding boundaries are Ollama and llama.cpp. Discovery is advisory; it never silently changes a configured model/provider. Sentence Transformers is a research/reference option; native inference requires future benchmark justification.

Repository knowledge is derived state rebuildable from source; legacy SQLite migration compatibility is not required. TreeIndex exposes structural navigation over graph knowledge while retaining non-tree relationships. Its schema/projection and TreeRouter's algorithm remain open architectural decisions.

The architectural source of truth is [SPEC.md](../spec/SPEC.md). [BOOTSTRAP.md](../BOOTSTRAP.md) gives future implementation phases and exit gates. This ADR establishes the top-level rewrite and refined product direction; it does not freeze tree/storage schemas, routing algorithms, transports, IDs, capsule fields, JEV API mappings, runner APIs, model heads, fusion, or inclusion algorithms. Detailed decisions belong in follow-up ADRs.

## Decision drivers

1. Code relationships and structural evidence need explicit domain identity and provenance.
2. Candidate recall, final context quality, and downstream success need separate optimization and evaluation.
3. Correctness-critical policy must be testable without models, integrations, or a database.
4. OXIDE resources and I/O need consistent lifecycle/session ownership while inference execution remains external.
5. TS integrations must consume one engine instead of implementing repository intelligence again.
6. Models, storage, and retrieval experiments must remain replaceable, benchmarked, and comparable to simple baselines.
7. Future decision datasets should arise from runtime capsule structures with matching distributions.
8. A solo-maintainable architecture should avoid compatibility burden and unnecessary package proliferation.
9. Structural routing needs first-class contracts and baselines from the start; similarity alone cannot define context.
10. Provider/model runners are replaceable infrastructure; configured identity and offline fallback must survive discovery changes or judge failures.

## Architecture overview

```text
TS control plane → typed Rust runtime service → Rust kernel
                                                ↓ domain port
                                          KnowledgeStore
                                                ↑ implemented by
                                  runtime-owned LadybugDB adapter
```

The kernel defines the storage/domain contracts. It does not depend on LadybugDB native values, IDs, query results, or Cypher. Runtime owns the adapter and connection resources. TS never accesses the repository store directly.

The main artifact flow is repository → TreeIndex → query → TreeRouter (lexical/vector entry points and structural exploration) → routed candidates / CandidateGraph / CandidateCapsules → DecisionProvider → deterministic Rust inclusion policy → structural expansion → ContextPacker → ContextBundle.

TreeRouter may also request bounded branch-value judgments through DecisionProvider during exploration. Rust owns traversal limits, thresholds, stopping rules, confidence routing, fallback, budgets, and final inclusion throughout; a judge supplies estimates. A heuristic provider keeps the complete structural path functional offline.

Runtime calls embedding/decision adapters; external Ollama/llama.cpp runners or explicitly configured judge services execute inference. Missing semantic configuration is a supported lexical/structural mode, surfaced as unavailable capability with optional setup UX. Hosted JEV is opt-in and cannot be required for CI or local-only operation. Existing JEV evaluation is inconclusive on validity and does not establish selection-quality improvement; integration and quality promotion are separate gates.

Official LadybugDB docs inspected on 2026-10-07 document embedded property graphs, a Rust `lbug` API, vector and FTS extensions, and one read/write database owner with connections from that owner. Rust extension linking, version/target packaging, index mutation, persistence, and concurrency behavior still require an OXIDE adapter spike. The capability observations and validation gates are recorded in SPEC.md; no database spike was run in this session.

## Consequences

### Positive consequences

- Product/domain structure drives the architecture rather than old storage and API accidents.
- TreeIndex, entry-point retrieval, TreeRouter, judgments, inclusion, expansion, and packing have inspectable artifacts and isolated tests.
- Offline deterministic behavior remains useful even if a model fails or never earns promotion.
- A narrow capsule seam aligns inference, diagnostics, and future DecisionBench data.
- Graph storage can change without rewriting domain identities or exposing native database objects to consumers.
- Integrations share consistent Rust behavior; the runtime can retain expensive resources across requests.
- No old DB migrations or permanent compatibility obligations are imported by default.
- External execution keeps model serving outside OXIDE; explicit identities make runner changes reviewable and prevent silent vector-space switches.
- JEV can be evaluated through a first-class optional boundary without making the architecture dependent on its availability or quality.

### Negative consequences / risks

- A clean rewrite incurs implementation cost and may temporarily offer fewer languages/integrations than the prototype.
- Graph-native storage does not itself establish better retrieval, selection, or downstream success.
- LadybugDB introduces native build/packaging, extension, persistence, and concurrency validation work; the initial choice may need reconsideration.
- Typed service and capsule contracts require versioning and discipline across Rust, TS, and datasets.
- Fine-grained resolved relationships can be expensive or ambiguous across languages; provenance and partial coverage must be visible.
- Domain-specific labels and repo-held-out evaluation remain major work before a learned decision model can ship.
- Dropping accidental compatibility needs an explicit user transition story before release, even though old indexes are rebuildable.
- Tree/forest projections must retain graph cross-edges and ambiguous ownership; routing may prune needed evidence and needs its own coverage/cost evaluation.
- External runner APIs, mutable model aliases and hosted-judge variability require identity/compatibility tests, deadlines, observable fallback and privacy configuration.

## Rejected alternatives

| Alternative | Reason for rejection as the rewrite foundation |
| --- | --- |
| Incrementally reshape the prototype while preserving SQLite schema/layout/APIs | Carries constraints that the clean rewrite explicitly seeks to remove; maintenance can continue separately |
| Port retrieval, selection, or indexing into TS | Creates a second repository engine and weakens deterministic Rust policy ownership |
| Treat vector top-K or reranker output as final context | Conflates discovery with task-specific, structurally coherent budget selection |
| Mandatory coding/general-purpose LLM or autonomous selection agent | Exceeds the product mission and makes offline fallback/policy measurability harder |
| Expose LadybugDB/Cypher/native node IDs through kernel APIs | Makes an implementation choice the domain identity and prevents clean fake-store contracts |
| Immediately split into many tiny crates/services | Adds build/API/runtime overhead before there is evidence for those boundaries |
| Add TreeIndex/TreeRouter only after similarity retrieval or model adoption | Makes foundational structural context construction an optional retrofit |
| Mandatory JEV/semantic inference or an OXIDE-owned model-serving stack | Breaks optional/offline behavior and assigns inference execution outside OXIDE's intended context-engineering scope |

These decisions do not rule out a different future storage adapter, fusion baseline, or well-evaluated specialized judge. Such changes need evidence and a focused ADR.

## Migration / transition strategy

1. Keep main and legacy files intact during architectural documentation. Do not bootstrap product code in this session.
2. Before later implementation, use the separate `rewrite/v2` branch and resolve required foundational decisions from SPEC.md. Do not mark this Proposed ADR Accepted automatically.
3. Inventory and pin historical fixtures, regressions, scorer corrections, raw dumps/manifests, negative results, and available reproduction environments. Missing local artifacts are explicit gaps.
4. Build fresh domain, TreeIndex/TreeRouter navigation, DecisionProvider and embedding-provider contracts with fake/heuristic implementations before real adapters. Plan optional JEV capabilities immediately; do not require a live judge. Port proven behavior/test intent selectively; each code reuse needs a spec-based justification.
5. Use an isolated new derived-store/cache namespace and rebuild from source. Do not read, migrate, overwrite, or delete the legacy SQLite index.
6. Establish v2 deterministic baselines and stage evaluations. Compare to the legacy binary through a test/evaluation adapter where useful, without preserving its architecture.
7. Advance bootstrap phases only after their exit gates. Foundational structural routing and provider contracts precede optional learned integration; learned promotion follows working deterministic context and fresh quality/cost evidence. Do not start Phase 0 during this documentation update.
8. Decide release naming, integration compatibility adapters, user upgrade UX, and branch merge/replacement separately. This proposal authorizes none of those publication actions.

## Validation criteria

- Kernel boundary tests/dependency checks exclude DB-native and integration types; TS consumers cannot implement selection or access storage directly.
- Fake and LadybugDB stores pass shared domain contracts, including scoped reads, bounded adjacency, identity, publication/recovery, and capability handling.
- Context construction works offline with no learned component and produces budget-valid, source-backed bundles with reasons.
- TreeIndex and deterministic TreeRouter participate in that offline path; lexical/vector retrieval supplies entry points rather than final context.
- Provider configuration persists explicit identity; runner detection cannot silently switch models. Ollama/llama.cpp adapters are tested behind contracts, without requiring a model-serving stack.
- JEV and branch-value judgments fit DecisionProvider from the start, remain optional, and never own code-correctness reasoning or deterministic policy.
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
- ADR-0007: TreeIndex / TreeRouter architecture.
- ADR-0008: Model runner / embedding provider boundary.
- ADR-0009: DecisionProvider and optional JEV integration, complementing policy/calibration in ADR-0005.

Identity, transport, token accounting, and a concrete inclusion algorithm may need separate ADRs when their alternatives are ready for review. TreeIndex/TreeRouter, runner APIs, and JEV integration details belong in the candidates above, not ADR-0001.
