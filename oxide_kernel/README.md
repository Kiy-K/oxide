# oxide_kernel — OXIDE v2 Rust core

All OXIDE v2 Rust lives here. Architecture: `docs/spec/SPEC.md`; phases:
`docs/BOOTSTRAP.md`; boundary and runtime model:
`docs/adr/0003-kernel-runtime-control-plane-boundary.md` (Proposed). The
Bun/TypeScript control plane is root `src/`. Phase 0 is in place: workspace,
boundaries and a minimal typed service seam. Phase 1 adds the kernel domain
contracts and an in-memory store (§ Phase 1 domain contracts). Phase 2A adds
source capture and the LadybugDB feasibility spike (§ Phase 2A). The partial
Phase 2B checkpoint adds the production adapter, capture/retention and external
repository registry; Python ingestion remains unfinished. See
[Phase 2B checkpoint](../docs/phase-2b.md). No routing algorithm or real model
provider has been added.

## Layout and dependency map

```text
src/  (Bun / TypeScript)                 CLI, MCP, integrations, orchestration, runtime supervision
   │  service contract v1 (contract/cases.json), provisional transport
   ▼
crates/runtime  (oxide-runtime, lib + bin)  sessions, DB/resource ownership, caches, provider clients
   │  plain Rust calls on kernel types
   ▼
crates/kernel   (oxide-kernel, lib)         repository intelligence, deterministic policy
```

| Concern (SPEC § Boundary ownership) | Home | Phase |
| --- | --- | --- |
| Domain types, TreeIndex, TreeRouter, capsules, inclusion policy, ContextPacker, KnowledgeStore *port* | `crates/kernel` | 1+ |
| Filesystem capture, DB adapter (LadybugDB), sessions, caches, scheduling, embedding/decision provider *clients*, wire types | `crates/runtime` | 1+ (wire types: now) |
| MCP, editor/agent adapters, CLI/config UX, process lifecycle, runtime supervision | root `src/` | later |
| Inference execution, weights, model serving | external runners (Ollama, llama.cpp, judge services) | never in OXIDE |

Two crates, because the kernel/runtime split must be a compilation boundary:
the kernel cannot link what the runtime links. A third crate needs a
demonstrated compilation, reuse, runtime or API reason (SPEC § System
architecture). This directory is its own Cargo workspace; Rust is pinned by
`rust-toolchain.toml` here, so run cargo from this directory (the Mise tasks
do).

### Enforced boundaries

| Check | Where | Fails when |
| --- | --- | --- |
| Kernel links no crates, has no build inputs | `crates/kernel/tests/boundary.rs` | the kernel `Cargo.toml` declares a dependency table (dev-dependencies allowed) or a `build`, `links` or `path` key |
| Kernel does no I/O | same | kernel `src/` names a std module outside a side-effect-free allowlist (grouped imports and `std as` aliases included), uses print/`dbg!`/`include!`/`#[path]`/`extern crate`; a self-test pins the known bypass shapes; `#![forbid(unsafe_code)]` closes the FFI route |
| Wire types stay out of the kernel | structural | serde/JSON live only in `crates/runtime` |
| Control plane is TS only and imports no engine | `src/boundary.test.ts` | `src/` holds anything but regular `.ts` files (symlinks included), or an import (parsed with Bun's transpiler: static, side-effect, re-export, dynamic, `require`) resolves outside `src/` other than the contract cases and isn't `bun:`/`node:`; computed `import()` and `require()` fail too |
| Contract agreement | `crates/runtime/tests/contract.rs` and `src/transport.test.ts` | the runtime's answer to a shared case differs from `contract/cases.json`, or TS cannot decode it through the real binary |

"No TS domain logic" cannot be fully machine-checked; the control plane
today only encodes requests, decodes envelopes and checks correlation.

## Service contract v1

Request: `{"version": 1, "id": "<string>", "op": "status"}`. Unknown fields
are rejected.

Response, always one JSON object:

- success: `{"version": 1, "id": "<id>", "ok": true, "result": {...}}`;
  `status` returns `{"kernel_version": "<semver>"}` from `oxide_kernel::status()`.
- error: `{"version": 1, "id": "<id>" | null, "ok": false, "error": {"code", "message"}}`.

`version` in a response is the protocol the runtime speaks. Error codes are
closed per version: `invalid_request` (not JSON/UTF-8, over 1 MiB, wrong
envelope; `id` is null when it could not be read), `unsupported_version`
(checked before the envelope shape, so a newer envelope gets this code) and
`unknown_operation`. Consumers branch on `ok` and `error.code`; `message` is
for humans. Any incompatible change bumps `PROTOCOL_VERSION` in both
`crates/runtime/src/lib.rs` and `src/contract.ts`, plus the cases.

TS distinguishes a contract error (`ok: false`, returned as data),
`ProtocolError` (reply is not a valid v1 response, or its id does not match)
and `TransportError` (spawn failure, non-zero exit or kill, non-JSON output).
An aborted call rejects with the `AbortSignal`'s reason (an `AbortError`), not
a transport error. A contract error with `id: null` (the runtime could not
read the id, e.g. an oversized request) is returned as that request's answer.

## Transport and lifecycle — provisional

Phase 0 runs **one `oxide-runtime` process per request**: request on stdin,
one response line on stdout, exit 0 for every contract response. The process
owns every resource for the life of one request (at most 1 MiB read, then
exit); an `AbortSignal` passed to `call`/`exchange` kills it. Only
`crates/runtime/src/main.rs` and `src/transport.ts` know this; `handle`/`serve`
and `src/contract.ts` are transport-free.

The intended model is a long-lived runtime supervised by Bun that owns
LadybugDB, sessions, caches and provider clients across requests. See ADR-0003
for that decision and its open questions (framing, multi-client ownership,
discovery, supervision). Do not build integrations on process-per-request.

## Commands

```bash
mise run verify       # what CI runs: rust:lint, rust:test, ts:install, ts:lint, ts:typecheck, ts:test
mise run rust:test    # kernel/runtime tests, kernel boundary checks, Rust side of the contract cases
mise run ts:test      # builds oxide-runtime, then bun test (boundary, decoder, contract cases via the binary)
```

After explicit `mise run native:prepare`, no network or model is needed for Rust tests.
Native preparation details are in `docs/agents/workflow.md`.

## Phase 1 domain contracts

| Contract | Where | Tests |
| --- | --- | --- |
| IDs, `SnapshotKey` | `kernel/src/id.rs` | unit tests there |
| Entities, typed relations, `SourceRef`, manifests, publication invariants | `kernel/src/knowledge.rs` | `kernel/tests/store_contract.rs` |
| `Query`, `QueryContext`, `ContextBudget` | `kernel/src/query.rs` | (plain data) |
| `KnowledgeStore` / `ReadView` port, `MemoryStore` | `kernel/src/store.rs` | `store_contract.rs`, generic over the store (`contract_suite!`) |
| TreeIndex projection 1 and bounded region navigation | `kernel/src/tree.rs` | `kernel/tests/routing_boundary.rs` |
| TreeRouter input/output/trace/limit types | `kernel/src/route.rs` | (types only; the algorithm is Phase 3) |
| DecisionProvider, capsule v1, heuristic, validation/fallback, shared allowance | `kernel/src/decision.rs` | `routing_boundary.rs` |
| Embedding space identity, runner client, batching, semantic status | `runtime/src/embedding.rs` | unit tests there (fake runner) |

The fixture repository is built in code (`kernel/tests/common/mod.rs`): it has
overloads, same-line nesting, a call cycle, ambiguous and unresolved targets,
logical containment, a partially parsed file and a heuristic test link.

### Identity policy

Accepted in [ADR-0010](../docs/adr/0010-domain-identity-and-source-capture.md),
which supersedes the Phase 1 proposal that stood here. In short: structural,
snapshot-local `FileId`/`SymbolId`/`ModuleId` (equal exactly when their inputs
are equal, no continuity across edits); content-addressed `sha256:` `Digest`,
`SnapshotId` and `DerivationId`; an assigned `RepoId`. `RepoPath` is
byte-exact and case-sensitive; case-fold collisions are reported by capture,
not rejected (the one change from Phase 1). Changing the policy changes types
in `id.rs` and bumps derivations, nothing else.

### Phase 1 decisions

- **Sync store port.** `KnowledgeStore` is synchronous with owned read views.
  The runtime can move calls off an executor; revisit if the pinned LadybugDB
  API needs async (Phase 2).
- **Stage/write/publish/discard.** Publication validates the whole generation
  (`knowledge::validate`, shared by every store). A failed publish stays
  staged and invisible. There is no "any derivation" lookup: `open` takes an
  exact key and reports `IncompatibleDerivation` with the derivations that do
  exist.
- **TreeIndex projection 1.** This is the minimal test hierarchy: one region
  per entity, organized by physical containment (repository ⊃ module/file,
  file ⊃ symbol ⊃ symbol). Publication requires each entity's single
  physical parent to be the one its ID implies (and its source to be in its
  own file), so the tree cannot contradict identity. Every other relation,
  logical containment included, is a typed cross-edge. Regions are computed
  in the kernel from typed adjacency, so fake and real stores share the
  navigation code. Materialized regions and richer groupings stay open.
- **Decisions.** Judgments correlate by subject, question and capsule
  version (no capsule digest yet). Missing, duplicate, out-of-range or
  non-finite values, wrong versions, abstentions, unsupported questions,
  provider errors, exhausted allowances and (when a floor is set) low or
  unreported confidence all fall back per subject to the heuristic, with a
  reason. Phase 1's heuristic is a neutral constant. The questions
  (`Relevance`, `NeighborValue`, `BranchValue`) are the candidate, neighbor
  and branch capabilities a JEV adapter maps to; nothing JEV-specific is in
  the kernel.
- **Embedding identity lives in the runtime** for now, since no kernel code
  consumes vectors before Phase 3. Space compatibility is `Same`, `Different`
  or `Unverified`; unknown fields never count as a match. Only the configured
  runner is probed. Discovered runners are listed as offers (unconfigured) or
  alternatives (configured) and are never substituted.
- **Deferred to their phases:** lexical/vector search operations and source
  hydration (Phase 2/3), the router algorithm (Phase 3), and SelectionPlan,
  ContextBundle and packer types (Phase 4).

## Phase 2A: feasibility and source capture

| Contract / evidence | Where | Tests |
| --- | --- | --- |
| Captured-source kernel input (`SourceCapture`) | `kernel/src/source.rs` | (plain data) |
| Runtime source capture: scope, skips, digests, `SnapshotId`, consistency retry | `runtime/src/capture.rs` | unit tests there |
| LadybugDB feasibility at lbug 0.21.2 | `spikes/ladybug/` (own workspace, not in `verify`; `mise run spike:ladybug`) | `spikes/ladybug/tests/feasibility.rs` |

Decisions: [ADR-0010](../docs/adr/0010-domain-identity-and-source-capture.md)
(identity, source capture) and
[ADR-0002](../docs/adr/0002-ladybugdb-knowledge-store.md) (pin, build,
ownership, publication protocol, physical schema), both Accepted by explicit
Phase 2B approval. The spike remains evidence only; the Phase 2B runtime
adapter links the pinned native library through explicit preparation.

## Open questions

| Question | Needed before |
| --- | --- |
| Long-lived runtime framing, multi-client ownership/discovery, supervision | real integrations; ADR-0003 |
| One schema generating both contract type sets | when the contract grows past a few operations |
| Python language conventions and completed ingestion/rebuild pipeline | Phase 2B; checkpoint in `docs/phase-2b.md` |
| First-publication interruption after seal, retained-generation footprint | Phase 2B; checkpoint in `docs/phase-2b.md` |
| Everything in SPEC § Open questions (IDs, TreeIndex projection, LadybugDB pin, capsule schema, tokenizer, ...) | Phase 1+ as listed there |

## Evidence inventory (historical main `ac985b28`, the parent of this branch's docs)

Reference assets kept on `rewrite/v2`, not code to port and not built:
`fixtures/benchmark.json`, `fixtures/{py_repo,ts_repo}`,
`fixtures/{conformance,protocol,structural_benchmark.json}`, `tests/`
(including `tests/benchmark_gate.rs`), `examples/`, `eval-agent/benchmark/`,
`scripts/agent_eval/`, `scripts/perf.sh`, and the reports SPEC § Historical
evidence cites under `docs/`. The legacy implementation they exercise was
removed from this branch; run them against the legacy binary built from
`main`/`ac985b28` (SPEC: "An unmodified legacy binary at a pinned revision may
serve as an external historical comparator"). Machine-local eval caches are
outside the repository. Port semantic assertions, not storage/API coupling.
