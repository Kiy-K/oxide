# Bootstrapping the OXIDE architectural rewrite

## Purpose and authority

This is guidance for future Codex/Claude implementation sessions. **This documentation session must not implement, scaffold, or refactor the rewrite.** OXIDE v2 names an architectural generation, not a SemVer release or a requirement to ship v0.2 first.

OXIDE is an open-source context engine for agents. OXIDE indexes repository knowledge into a structural TreeIndex and routes each task through that structure to construct a bounded, high-value ContextBundle for software agents. Its mission remains: **OXIDE transforms repository structure into task-specific context.** Agents consume context; OXIDE remains agent-agnostic and is not an autonomous coding agent.

Use the foundational flow: repository → TreeIndex → query → TreeRouter (lexical/vector entry points) → CandidateGraph / CandidateCapsules → DecisionProvider → deterministic Rust policy → structural expansion → ContextPacker → ContextBundle. Routing may also request bounded branch-value judgments through DecisionProvider. Do not postpone TreeIndex/TreeRouter until after similarity retrieval or model adoption.

Before implementing, read [SPEC.md](spec/SPEC.md), all **Accepted** ADRs, root `AGENTS.md`, and applicable scoped instructions. Also read [ADR-0001](adr/0001-oxide-v2-rewrite.md), which is currently **Proposed**. Read any other Proposed ADR relevant to the work, but do not treat it as Accepted or change its status automatically.

The user’s rewrite instruction and SPEC.md supersede legacy maintenance assumptions about preserving SQLite, IDs, APIs, and module layout. Other repository working rules still apply, including documentation evidence, scope discipline, and no unrequested commits/pushes/releases. Resolve conflicts between SPEC.md and an Accepted ADR in writing before dependent implementation.

## Non-negotiable implementation rules

1. Work on the separate rewrite branch `rewrite/v2` when implementation is authorized. Inspect branch and working-tree state first; protect existing work. Do not broadly refactor main.
2. Existing main is historical evidence, not an architecture template. Do not port modules/code by default. Reuse requires a stated spec requirement, a boundary-fit explanation, and appropriate tests.
3. Do not preserve legacy SQLite schemas, row IDs, migrations, cache locations, or accidental public APIs. New derived state uses an isolated namespace and is rebuildable from captured source.
4. When spec and old code disagree, follow the spec. When the spec is ambiguous, record the ambiguity, affected phase, alternatives, and proposed decision. Continue independent work; do not invent a permanent ID/schema/transport/model policy to unblock scaffolding.
5. Keep semantic embeddings, JEV, and all learned judges optional. Preserve deterministic lexical entry points, structural TreeRouter exploration, heuristic DecisionProvider and ContextPacker with no runner/model/network requirement.
6. Models judge; deterministic Rust decides. Rust owns thresholds, traversal, routing/stopping, expansion, inclusion, budget, fallback, and final packing. Never overwrite retrieval scores with logits/probabilities from another score space. JEV judges relevance/value/branch/neighbor usefulness and uncertainty, not code correctness.
7. Put TreeIndex/TreeRouter and repository intelligence in the kernel; put OXIDE I/O/resources/provider request orchestration in runtime; put integrations/presentation in TS. TS must not become a second router, selector, or source parser.
8. Do not expose LadybugDB objects, native IDs, result rows, or Cypher through kernel, capsule, or TS service contracts. Implement the kernel-owned KnowledgeStore contract in runtime infrastructure.
9. Start as a modular monolith. Logical kernel/runtime separation is mandatory; many tiny crates/packages are not. Justify each new compilation, reuse, runtime, or API boundary.
10. Add meaningful tests at every architectural boundary and stage. Test declared invariants, degraded states, cancellation/publication, and limits rather than mirroring implementation details.
11. Maintain frozen measurable baselines, raw stage artifacts, and provenance. Do not regenerate gold or erase negative results to make a challenger pass.
12. For every feature, state what information it adds, which decision it improves, and which metric should move. Require evidence before promotion to defaults.
13. Inspect current official technology documentation at the pinned release. Documentation capability claims are not proof that the Rust adapter builds, loads extensions, or handles mutations safely.
14. Do not execute repository code/build hooks as part of context indexing. Respect repository scope and source privacy at hydration, expansion, model, trace, and export boundaries.
15. Structure before similarity. Retrieval finds entry points; routing finds context. TreeIndex retains graph cross-edges; do not force all repository relationships into a single-parent tree. Specify a minimal structural projection/navigation contract without prematurely freezing its physical schema or routing algorithm.
16. Design DecisionProvider support from the beginning for heuristic, optional JEV, future local specialized, and optional stronger judges. Keep subject correlation, uncertainty/abstention, request limits and deterministic fallback in the initial contracts; no live JEV is required for CI/bootstrap.
17. OXIDE owns context engineering, not model serving. External runners execute inference. Prefer Ollama and llama.cpp embedding adapter boundaries; Sentence Transformers is research/reference infrastructure, and native inference is a future benchmark-driven exception.
18. Discovery is advisory. Unconfigured semantics remains unavailable with lexical/structural operation and optional setup choices. Persist configured provider/model identity; never switch because another runner appears. Validate explicit reconfiguration and invalidate incompatible vectors. Do not install weights or start inference serving as an automatic discovery side effect.

## Before Phase 0

Record the requested scope, branch/base revision, applicable instructions, and unresolved decisions from SPEC.md. Confirm later implementation is actually authorized; this document itself is not a request to start coding.

Create an evidence inventory referencing the inspected historical main revision `ac985b28fcff7663aedadf2db5b40aa2dc325580` or a newly inspected pin. Preserve fixture/language/relation tests, embedding identity/staleness/recovery cases, ContextBench scorer fixes, evaluation scripts/raw artifacts, performance harnesses, and rejected experiments. Check whether artifacts are committed, machine-local, or dependent on retired experimental code. Preserve references; do not copy the entire old source tree into a new skeleton.

Historical targets include `fixtures/benchmark.json`, `fixtures/py_repo`, `fixtures/ts_repo`, language/relation conformance data, `eval-agent/benchmark/`, `scripts/agent_eval/`, `scripts/perf.sh`, and the evidence reports listed in SPEC.md. Legacy test code may embed old IDs/CLI/schema assumptions; port its semantic assertions rather than its storage/API coupling.

## Bootstrap phases and exit gates

The phases are an initial dependency order, not immutable project management. A change of order needs a short rationale, updated dependencies, and unchanged correctness gates. TreeIndex/TreeRouter, DecisionProvider (including JEV capability planning), and embedding-provider contracts begin in Phase 1 with evaluation, trace, uncertainty/fallback and fixture artifacts. The complete deterministic path precedes learned promotion; do not defer foundational routing/provider design to Phase 5.

### Phase 0 — Minimal workspace and boundaries

Deliver:

- A minimal Rust workspace/host with kernel and runtime responsibilities separated in dependencies and tests.
- A TS workspace for the control-plane boundary, without importing legacy integrations wholesale.
- A documented minimal versioned request/result/error contract and resource-owner lifecycle. Choose a minimal local test transport explicitly; do not silently freeze daemon or native-addon architecture.
- A dependency map placing TreeIndex, TreeRouter and ContextPacker in the kernel, provider clients/orchestration in runtime, and inference execution outside OXIDE. No model-serving stack or native model dependency is scaffolded.
- Pinned toolchains/dependency lockfiles, Mise tasks, and CI for formatting/linting, Rust tests, TS type checks/tests, boundary checks, and cross-language contract tests.

Exit gate:

- A typed request reaches a Rust stub/domain operation and returns a validated result/error without TS domain logic or kernel integration imports.
- Build/test tasks are reproducible and CI matches Mise commands. No network or model is required for kernel tests.
- Package/crate choices and unresolved transport/ownership issues are documented.

### Phase 1 — Domain contracts and fake store

Deliver:

- Repository/snapshot/derivation and domain ID types, entities, relations, source references, query context, structural regions, entry-point/routed candidate/plan/bundle concepts, and typed errors.
- A documented ID/namespace policy covering overloads, nesting, duplicate names, byte-range attribution, case/path rules, and collision handling before relying on identity in ingestion.
- KnowledgeStore read/write/publication/capability contracts and a fake/in-memory store.
- TreeIndex projection/navigation and TreeRouter input/output/trace/limit contracts, preserving graph cross-edges and partial coverage. Start with a minimal documented test hierarchy; physical schema and advanced routing remain open.
- An initial typed DecisionProvider and bounded candidate/region capsule contract with deterministic heuristic/fake providers, confidence/abstention, subject/version correlation, and fallback. Identify JEV candidate/branch/neighbor judgment capabilities now; no code-correctness reasoner or live API dependency.
- Embedding-provider identity/capability/batching/error contracts with fake runner fixtures; plan Ollama/llama.cpp adapters, advisory discovery, persisted identity and explicit reconfiguration.
- Contract tests and deterministic manifests/fixtures for scoped lookup, adjacency bounds, completeness, incompatible derivations, and publication visibility.

Exit gate:

- Kernel operations run against the fake store with no database/model/integration.
- IDs and lookup scope are unambiguous and no DB-native type enters the domain.
- Failed/unpublished generations cannot appear as valid current snapshots; capabilities and missingness are explicit.
- Minimal TreeIndex navigation and TreeRouter/DecisionProvider boundary fixtures run offline, with cycles/cross-edges, missing/uncertain judgments and declared traversal limits. JEV/provider capabilities do not leak vendor types or define Rust policy.
- Unconfigured semantics, unavailable configured runner, and newly discovered alternative runner have distinct states; none triggers a silent model/provider switch.

### Phase 2 — LadybugDB adapter and ingestion

Deliver:

- A pinned LadybugDB/Rust adapter feasibility record: native build/target packaging, offline build, extension linking/loading, persistence/reopen, concurrency/ownership, cancellation, and mutation/rebuild observations.
- A physical graph schema mapped to domain identity/provenance and TreeIndex navigation through KnowledgeStore. Resolve node/facet representation for tests and ambiguous/unresolved references without treating the tree projection as all graph knowledge.
- Runtime source capture/scope enforcement and repository/file/symbol-to-TreeIndex ingestion for an explicitly chosen language slice, with conservative relation extraction and coverage diagnostics.
- Atomic publication and safe discard/recovery of incomplete generations; a separately scoped derived-store location.

Exit gate:

- Fake and real stores pass the common contract suite for supported operations.
- TreeIndex membership/navigation and cross-edges agree with the published entity/source snapshot; a request cannot mix projections/generations.
- Restart/rebuild, deletes, partial source, duplicate names/overloads, same-line nesting, and failed publication preserve intended logical knowledge.
- One runtime owns read/write DB resources; a second client cannot open an unsafe independent owner. Supported concurrency is documented with tests at the pinned API.
- Unknown vector/FTS update or Rust extension behavior remains a blocked capability, never an assumed success.

Start with full rebuild if necessary. Add incremental indexing later with full/incremental parity before advertising it; do not import migrations to accelerate this phase.

### Phase 3 — Entry-point retrieval, TreeRouter and evaluation baseline

Deliver:

- Deterministic lexical and structural-seed retrieval that returns an entry-point CandidateSet; optional vector retrieval with explicit embedding-space identity through external Ollama/llama.cpp adapters.
- A deterministic bounded TreeRouter baseline over TreeIndex, including root/scope fallback, heuristic branch judgments through the Phase 1 contract, visited sets/stopping limits, minimal routed candidates/capsules and route traces. Refine schemas explicitly; advanced algorithms remain optional experiments.
- Provider configuration/health/readiness and bounded local discovery, batch/identity compatibility and deadline behavior. External runners execute inference; detection never enables semantics or changes persisted configuration by itself.
- A stage evaluation harness, gold-to-domain mapping, corrected scorer provenance, cold/warm indexing/query measurements, entry-point channel/fusion dumps and routing coverage/pruning/cost traces.
- Frozen lexical-only entry-point and weighted-RRF controls plus deterministic structural-routing controls; optional semantic-only and exact-vector controls. A no-routing similarity path is an evaluation ablation, not normal architecture. Treat historical K=60 / 0.6–0.4 as a comparison configuration, not a v2 default requirement.
- Tested explicit fallback for unavailable/invalid/incompatible semantic evidence.

Exit gate:

- Retrieval finds entry points; TreeRouter constructs the structural candidate universe. Neither returns final packed context or hides channel failures.
- Snapshot filters precede candidate limits; score/rank semantics, ordering, cutoffs, and hydration identity are reproducible.
- Recall@K, MRR, entry-point coverage, routed required-entity/region coverage, pruning loss and routing work can be measured separately from inclusion/packing outcomes; ANN quality is compared to exact search when enabled.
- No semantic configuration, missing runner, model-identity drift and additional discovered runner all preserve observable lexical/structural routing and never silently substitute providers. Ollama/llama.cpp adapter tests can use fixtures without executing inference in CI.
- Frozen manifests/raw results survive later experiments. Fixture passing is reported as a regression floor, not generalization proof.

Implement deterministic TreeIndex/TreeRouter and lexical entry points first; vector extensions, JEV and learned inference are not prerequisites for offline context.

### Phase 4 — Routed evidence, optional JEV and deterministic context

Deliver:

- CandidateGraph of routed evidence with bounded nodes/edges/fanout/depth, stable order, TreeIndex/route provenance, cycle handling, and truncation diagnostics.
- Refined versioned CandidateCapsule and region/branch subject semantics plus canonical dataset/inference rendering, evolving the earlier provider contract with explicit size/missingness/truncation rules.
- Heuristic candidate/branch decisions, a simple greedy inclusion baseline, and a SelectionPlan with reasons and coherent evidence groups. TreeRouter exploration and final inclusion remain distinct.
- Optional JEV adapter design/implementation against the existing DecisionProvider contract, with narrow candidate/branch/neighbor value questions, version/response validation, uncertainty, deadlines, request limits, privacy and fake-service fixtures. Live use is opt-in and requires current API/capability verification; support does not imply quality promotion or code-correctness reasoning.
- Bounded structural expansion and kernel ContextPacker with a declared canonical payload/tokenizer, overlap deduplication, view reduction, and omission reporting.

Exit gate:

- End-to-end deterministic offline context uses TreeIndex/TreeRouter/heuristic DecisionProvider/ContextPacker without any learned component, JEV, or inference runner. Optional JEV work does not block the deterministic phase exit.
- Entry-point retrieval, TreeIndex navigation, TreeRouter, graph, capsules, decisions, plan, expansion, and final bundle can be independently inspected/replayed.
- Capsule/dataset rendering agrees; no gold/final policy outcome enters model input.
- Graph membership is distinct from inclusion; expanded neighbors compete within the same budget and terminate under limits.
- Packer handles zero/tiny budgets, oversized entities, overlapping source, and unavailable prerequisites while satisfying the declared token budget and coherence rules.
- Required-symbol recall, labeled context noise, tokens, coherence and cost are measurable against no-expansion/heuristic/greedy controls.
- JEV disabled, failed, malformed, uncalibrated or abstaining leaves bounded heuristic routing/inclusion functional; captured provider responses replay deterministically even if the service itself is nondeterministic.

TreeIndex/TreeRouter are foundational concepts with open schema/algorithm decisions. TreeSelect, if retained as a name for an inclusion experiment, is separate from TreeRouter and remains unspecified until objective, TreeIndex view, prerequisites, complexity, and evaluation are documented. Greedy inclusion plus deterministic bounded routing is sufficient for the baseline; do not implement an unspecified optimizer.

### Phase 5 — Judge specialization, calibration and DecisionBench

Deliver:

- Optional local specialized judge and stronger-judge escalation experiments through the existing DecisionProvider/subject contract. The foundational interface, JEV capability planning and uncertainty/fallback have already been designed in earlier phases.
- Runtime provider-client orchestration and shared routing/candidate request limits, timeouts and explicit privacy/cost rules. External runners own inference execution; native execution needs separate benchmark justification.
- DecisionBench record/provenance/split design aligned to runtime candidate/branch capsules; realistic entry-point/routed/branch hard negatives, teacher uncertainty/audit, visitation-bias controls, and repository-held-out calibration/test procedures. Sentence Transformers may be a research/reference tool.
- Separate branch/candidate evaluation for discrimination, validity/repeatability, calibration, risk-coverage, position bias, fallback/routing, and end-to-end request/runner cost; preserve no-model and heuristic controls. Existing JEV validity failure is not evidence of either quality gain or quality rejection.

Exit gate:

- Disabled, failed, malformed, uncertain, or unavailable learned inference all leave deterministic context construction functional and observable.
- Models cannot alter traversal rules, thresholds, expansion, hard inclusion, budget, or fallback. Judgments remain separate from entry-point scores and TreeRouter/inclusion/packing policy outcomes; no judge is treated as a code-correctness reasoner.
- Dataset records use the same input semantics/renderer as inference, and policy decisions are not mislabeled as ground-truth relevance.
- No learned model is promoted without a frozen repository-held-out quality/calibration/cost protocol. A trained checkpoint is not required to complete this architectural interface phase.

## Boundary validation matrix

| Boundary | Essential assertions |
| --- | --- |
| TS ↔ runtime | Versioned types/errors, request validation, cancellation/lifecycle; no prose parsing or TS selection |
| Runtime ↔ kernel | Integration-free domain input/output; resource/provider failures explicit; effective policy stays Rust |
| Runtime ↔ external runners | Configured model/provider identity, batch semantics and deadlines; advisory discovery/no automatic switches; semantic-unavailable fallback, no serving ownership |
| Kernel ↔ KnowledgeStore | Domain IDs, snapshot-scoped views, bounds, provenance, atomic publication; same fake/real contracts |
| Knowledge ↔ TreeIndex ↔ TreeRouter | Snapshot/projection-consistent regions/cross-edges, bounded navigation, entry-point/root fallback, stable traversal/pruning trace; heuristic routing works offline |
| Captured source ↔ knowledge | Byte/digest/range fidelity, unsupported/partial parsing, overload/nesting identity; full/incremental convergence when enabled |
| CandidateGraph ↔ capsule | Stable bounded encoding, truncation/missingness, no DB internals or hidden labels |
| Capsule ↔ DecisionProvider | Candidate/region/schema/digest correlation, invalid-value rejection, uncertainty/abstention/failure fallback; JEV optional/narrow, no policy or correctness reasoning; training/runtime parity |
| Plan/expansion ↔ bundle | Coherent prerequisites, cycle/fanout limits, source deduplication, strict declared budget, stable ordering/omission reasons |

## Baselines and promotion

Keep lexical-only entry-point retrieval, optional semantic-only, frozen RRF, exact-vector control, deterministic bounded TreeRouter, heuristic/no-model judgments, greedy inclusion, no-expansion packing, and an external no-OXIDE downstream-agent control where appropriate. Use no-routing similarity as a diagnostic ablation only; removing learned judges never removes foundational routing. Freeze manifests, not just aggregate tables.

Report entry-point retrieval, structural routing, context quality, and downstream success separately. Preserve scorer changes and artifact limitations. Historic Qwen3, Arctic, Laya, Julia, JEV, CodeGraph, and ch5 findings apply to their pins and gates; they are neither universal bans nor proof that the new architecture will work. JEV's typed-evidence validity failure left quality gates unread; require fresh validity/calibration/cost evidence before promotion.

Before promoting an experiment, state the information/decision/metric hypothesis and preregister paired quality/cost gates, held-out split, uncertainty method, and rollback to baseline. Do not retrofit gates after seeing test results.

## Follow-up decisions and handoff

Proposed ADR candidates are 0002 (LadybugDB), 0003 (layer/service boundary), 0004 (CandidateCapsule), 0005 (judgments and deterministic policy), 0006 (rebuildable state/no legacy migration), 0007 (TreeIndex / TreeRouter architecture), 0008 (model runner / embedding provider boundary), and 0009 (DecisionProvider and optional JEV integration, complementing policy/calibration in 0005). Identity, transport, token accounting, and concrete inclusion algorithms may need additional focused ADRs. SPEC.md lists the questions and the phase where each must be resolved. None of these candidates is automatically Accepted.

At the end of every future implementation phase, leave: changed scope/boundaries, tests and exact validation evidence, frozen benchmark manifests, unresolved questions/blocked capabilities, and the next authorized phase. Read the full diff for accidental source/dependency/generated-file changes. Do not commit, push, release, change ADR status, or begin unrelated follow-up work without authorization.
