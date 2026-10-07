# Bootstrapping the OXIDE architectural rewrite

## Purpose and authority

This is guidance for future Codex/Claude implementation sessions. **This documentation session must not implement, scaffold, or refactor the rewrite.** OXIDE v2 names an architectural generation, not a SemVer release or a requirement to ship v0.2 first.

Before implementing, read [SPEC.md](spec/SPEC.md), all **Accepted** ADRs, root `AGENTS.md`, and applicable scoped instructions. Also read [ADR-0001](adr/0001-oxide-v2-rewrite.md), which is currently **Proposed**. Read any other Proposed ADR relevant to the work, but do not treat it as Accepted or change its status automatically.

The user’s rewrite instruction and SPEC.md supersede legacy maintenance assumptions about preserving SQLite, IDs, APIs, and module layout. Other repository working rules still apply, including documentation evidence, scope discipline, and no unrequested commits/pushes/releases. Resolve conflicts between SPEC.md and an Accepted ADR in writing before dependent implementation.

## Non-negotiable implementation rules

1. Work on a separate rewrite branch, tentatively `rewrite/v2`, when implementation is authorized. Inspect branch and working-tree state first; protect existing work. Do not broadly refactor main.
2. Existing main is historical evidence, not an architecture template. Do not port modules/code by default. Reuse requires a stated spec requirement, a boundary-fit explanation, and appropriate tests.
3. Do not preserve legacy SQLite schemas, row IDs, migrations, cache locations, or accidental public APIs. New derived state uses an isolated namespace and is rebuildable from captured source.
4. When spec and old code disagree, follow the spec. When the spec is ambiguous, record the ambiguity, affected phase, alternatives, and proposed decision. Continue independent work; do not invent a permanent ID/schema/transport/model policy to unblock scaffolding.
5. Keep learned embeddings and decision models optional. Preserve a functional deterministic lexical/structural retrieval and heuristic-selection path with no network requirement.
6. ML returns judgments. Rust owns thresholds, traversal, routing, inclusion, budget, fallback, and final packing. Never overwrite retrieval scores with logits/probabilities from another score space.
7. Put repository intelligence in the kernel; put I/O and expensive resources in runtime; put integrations/presentation in TS. TS must not become a second selector or source parser.
8. Do not expose LadybugDB objects, native IDs, result rows, or Cypher through kernel, capsule, or TS service contracts. Implement the kernel-owned KnowledgeStore contract in runtime infrastructure.
9. Start as a modular monolith. Logical kernel/runtime separation is mandatory; many tiny crates/packages are not. Justify each new compilation, reuse, runtime, or API boundary.
10. Add meaningful tests at every architectural boundary and stage. Test declared invariants, degraded states, cancellation/publication, and limits rather than mirroring implementation details.
11. Maintain frozen measurable baselines, raw stage artifacts, and provenance. Do not regenerate gold or erase negative results to make a challenger pass.
12. For every feature, state what information it adds, which decision it improves, and which metric should move. Require evidence before promotion to defaults.
13. Inspect current official technology documentation at the pinned release. Documentation capability claims are not proof that the Rust adapter builds, loads extensions, or handles mutations safely.
14. Do not execute repository code/build hooks as part of context indexing. Respect repository scope and source privacy at hydration, expansion, model, trace, and export boundaries.

## Before Phase 0

Record the requested scope, branch/base revision, applicable instructions, and unresolved decisions from SPEC.md. Confirm later implementation is actually authorized; this document itself is not a request to start coding.

Create an evidence inventory referencing the inspected historical main revision `ac985b28fcff7663aedadf2db5b40aa2dc325580` or a newly inspected pin. Preserve fixture/language/relation tests, embedding identity/staleness/recovery cases, ContextBench scorer fixes, evaluation scripts/raw artifacts, performance harnesses, and rejected experiments. Check whether artifacts are committed, machine-local, or dependent on retired experimental code. Preserve references; do not copy the entire old source tree into a new skeleton.

Historical targets include `fixtures/benchmark.json`, `fixtures/py_repo`, `fixtures/ts_repo`, language/relation conformance data, `eval-agent/benchmark/`, `scripts/agent_eval/`, `scripts/perf.sh`, and the evidence reports listed in SPEC.md. Legacy test code may embed old IDs/CLI/schema assumptions; port its semantic assertions rather than its storage/API coupling.

## Bootstrap phases and exit gates

The phases are an initial dependency order, not immutable project management. A change of order needs a short rationale, updated dependencies, and unchanged correctness gates. Evaluation manifests, traces, and deterministic fixture artifacts begin in Phase 1. Do not defer them until a model exists.

### Phase 0 — Minimal workspace and boundaries

Deliver:

- A minimal Rust workspace/host with kernel and runtime responsibilities separated in dependencies and tests.
- A TS workspace for the control-plane boundary, without importing legacy integrations wholesale.
- A documented minimal versioned request/result/error contract and resource-owner lifecycle. Choose a minimal local test transport explicitly; do not silently freeze daemon or native-addon architecture.
- Pinned toolchains/dependency lockfiles, Mise tasks, and CI for formatting/linting, Rust tests, TS type checks/tests, boundary checks, and cross-language contract tests.

Exit gate:

- A typed request reaches a Rust stub/domain operation and returns a validated result/error without TS domain logic or kernel integration imports.
- Build/test tasks are reproducible and CI matches Mise commands. No network or model is required for kernel tests.
- Package/crate choices and unresolved transport/ownership issues are documented.

### Phase 1 — Domain contracts and fake store

Deliver:

- Repository/snapshot/derivation and domain ID types, entities, relations, source references, query context, candidate/plan/bundle concepts, and typed errors.
- A documented ID/namespace policy covering overloads, nesting, duplicate names, byte-range attribution, case/path rules, and collision handling before relying on identity in ingestion.
- KnowledgeStore read/write/publication/capability contracts and a fake/in-memory store.
- Contract tests and deterministic manifests/fixtures for scoped lookup, adjacency bounds, completeness, incompatible derivations, and publication visibility.

Exit gate:

- Kernel operations run against the fake store with no database/model/integration.
- IDs and lookup scope are unambiguous and no DB-native type enters the domain.
- Failed/unpublished generations cannot appear as valid current snapshots; capabilities and missingness are explicit.

### Phase 2 — LadybugDB adapter and ingestion

Deliver:

- A pinned LadybugDB/Rust adapter feasibility record: native build/target packaging, offline build, extension linking/loading, persistence/reopen, concurrency/ownership, cancellation, and mutation/rebuild observations.
- A physical graph schema mapped to domain identity and provenance through KnowledgeStore. Resolve node/facet representation for tests and ambiguous/unresolved references.
- Runtime source capture/scope enforcement and repository/file/symbol ingestion for an explicitly chosen language slice, with conservative relation extraction and coverage diagnostics.
- Atomic publication and safe discard/recovery of incomplete generations; a separately scoped derived-store location.

Exit gate:

- Fake and real stores pass the common contract suite for supported operations.
- Restart/rebuild, deletes, partial source, duplicate names/overloads, same-line nesting, and failed publication preserve intended logical knowledge.
- One runtime owns read/write DB resources; a second client cannot open an unsafe independent owner. Supported concurrency is documented with tests at the pinned API.
- Unknown vector/FTS update or Rust extension behavior remains a blocked capability, never an assumed success.

Start with full rebuild if necessary. Add incremental indexing later with full/incremental parity before advertising it; do not import migrations to accelerate this phase.

### Phase 3 — Candidate retrieval and evaluation baseline

Deliver:

- Deterministic lexical and structural-seed retrieval that returns CandidateSet; optional vector retrieval with explicit embedding-space identity.
- A stage evaluation harness, gold-to-domain mapping, corrected scorer provenance, cold/warm indexing/query measurements, and candidate channel/fusion dumps.
- Frozen lexical-only and weighted-RRF controls; optional semantic-only and exact-vector controls. Treat historical K=60 / 0.6–0.4 as a comparison configuration, not a v2 default requirement.
- Tested explicit fallback for unavailable/invalid/incompatible semantic evidence.

Exit gate:

- Retrieval does not return final packed context or hide channel failures.
- Snapshot filters precede candidate limits; score/rank semantics, ordering, cutoffs, and hydration identity are reproducible.
- Recall@K, MRR, and candidate gold coverage can be measured separately from selector outcomes; ANN quality is compared to exact search when enabled.
- Frozen manifests/raw results survive later experiments. Fixture passing is reported as a regression floor, not generalization proof.

Implement deterministic candidates first; vector extensions are not prerequisites for offline context.

### Phase 4 — Graph, capsules, deterministic context

Deliver:

- CandidateGraph with bounded nodes/edges/fanout/depth, stable ordering, provenance, cycle handling, and truncation diagnostics.
- Versioned CandidateCapsule semantic schema and canonical dataset/inference rendering, with explicit size/missingness/truncation rules.
- Heuristic CandidateDecisions, a simple greedy selector baseline, and a SelectionPlan with reasons and coherent evidence groups.
- Bounded structural expansion and a source-backed context packer with a declared canonical payload/tokenizer, overlap deduplication, view reduction, and omission reporting.

Exit gate:

- End-to-end deterministic offline context works without any learned component.
- Retrieval, graph, capsules, decisions, plan, expansion, and final bundle can be independently inspected/replayed.
- Capsule/dataset rendering agrees; no gold/final policy outcome enters model input.
- Graph membership is distinct from inclusion; expanded neighbors compete within the same budget and terminate under limits.
- Packer handles zero/tiny budgets, oversized entities, overlapping source, and unavailable prerequisites while satisfying the declared token budget and coherence rules.
- Required-symbol recall, labeled context noise, tokens, coherence and cost are measurable against no-expansion/heuristic/greedy controls.

TreeSelect remains a working name until objective, graph projection, prerequisites, complexity, and evaluation are documented. A greedy baseline is sufficient to establish the seam; do not implement an unspecified optimizer.

### Phase 5 — Optional learned judgment and DecisionBench seam

Deliver:

- A removable typed DecisionProvider interface with model/session identity, schema validation, candidate correlation, uncertainty/abstention, and per-candidate heuristic fallback.
- Model/session lifecycle in runtime, with bounded inference, timeouts, and explicit privacy/cost rules for optional stronger judging.
- DecisionBench record/provenance/split design aligned to runtime capsules; realistic retrieved hard negatives, teacher uncertainty/audit, and repository-held-out calibration/test procedures.
- Evaluation for discrimination, calibration, risk-coverage, position bias, fallback/routing, and CPU cost; preserve no-model and heuristic controls.

Exit gate:

- Disabled, failed, malformed, uncertain, or unavailable learned inference all leave deterministic context construction functional and observable.
- Models cannot alter traversal rules, thresholds, hard inclusion, budget, or fallback. Judgments remain separate from retrieval scores and Rust policy outcomes.
- Dataset records use the same input semantics/renderer as inference, and policy decisions are not mislabeled as ground-truth relevance.
- No learned model is promoted without a frozen repository-held-out quality/calibration/cost protocol. A trained checkpoint is not required to complete this architectural interface phase.

## Boundary validation matrix

| Boundary | Essential assertions |
| --- | --- |
| TS ↔ runtime | Versioned types/errors, request validation, cancellation/lifecycle; no prose parsing or TS selection |
| Runtime ↔ kernel | Integration-free domain input/output; resource/provider failures explicit; effective policy stays Rust |
| Kernel ↔ KnowledgeStore | Domain IDs, snapshot-scoped views, bounds, provenance, atomic publication; same fake/real contracts |
| Captured source ↔ knowledge | Byte/digest/range fidelity, unsupported/partial parsing, overload/nesting identity; full/incremental convergence when enabled |
| CandidateGraph ↔ capsule | Stable bounded encoding, truncation/missingness, no DB internals or hidden labels |
| Capsule ↔ decision | Schema/candidate/digest correlation, invalid-value rejection, abstention/failure fallback; training/runtime parity |
| Plan/expansion ↔ bundle | Coherent prerequisites, cycle/fanout limits, source deduplication, strict declared budget, stable ordering/omission reasons |

## Baselines and promotion

Keep lexical-only retrieval, optional semantic-only, frozen RRF, exact-vector control, heuristic/no-model judgments, greedy selection, no-expansion packing, and an external no-OXIDE downstream-agent control where appropriate. Freeze manifests, not just aggregate tables.

Report retrieval quality, context quality, and downstream success separately. Preserve scorer changes and artifact limitations. Historic Qwen3, Arctic, Laya, Julia, CodeGraph, and ch5 findings apply to their pins and gates; they are neither universal bans nor proof that the new architecture will work.

Before promoting an experiment, state the information/decision/metric hypothesis and preregister paired quality/cost gates, held-out split, uncertainty method, and rollback to baseline. Do not retrofit gates after seeing test results.

## Follow-up decisions and handoff

Proposed ADR candidates are 0002 (LadybugDB), 0003 (layer/service boundary), 0004 (CandidateCapsule), 0005 (judgments and deterministic policy), and 0006 (rebuildable state/no legacy migration). Identity, transport, token accounting, and concrete selection algorithms may need additional focused ADRs. SPEC.md lists the questions and the phase where each must be resolved.

At the end of every future implementation phase, leave: changed scope/boundaries, tests and exact validation evidence, frozen benchmark manifests, unresolved questions/blocked capabilities, and the next authorized phase. Read the full diff for accidental source/dependency/generated-file changes. Do not commit, push, release, change ADR status, or begin unrelated follow-up work without authorization.
