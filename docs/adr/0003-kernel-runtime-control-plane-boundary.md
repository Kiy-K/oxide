# ADR-0003: Rust kernel/runtime and Bun control-plane boundary

## Status

**Proposed** — 2026-10-07. Follow-up to [ADR-0001](0001-oxide-v2-rewrite.md) (also Proposed), listed there as "Rust kernel/runtime and TypeScript control-plane boundary". Records the Phase 0 layout and the intended runtime model; it does not freeze a wire framing.

## Context

[SPEC.md](../spec/SPEC.md) assigns repository intelligence and deterministic policy to a Rust kernel, I/O/resources/sessions/provider clients to a Rust runtime, and integrations/orchestration/presentation to a TypeScript control plane. It leaves the service transport and multi-client ownership open ("Phase 0 minimal boundary; before real integrations"). Phase 0 needed a working typed seam without committing to one.

The runtime must eventually own long-lived resources: one read/write LadybugDB owner per store, repository sessions, snapshot-scoped caches, and embedding/decision provider clients (SPEC § Runtime). A process that exits after every request cannot hold any of these.

## Decision

Repository layout on `rewrite/v2`:

| Path | Role |
| --- | --- |
| `src/` | Bun/TypeScript control plane: CLI UX, MCP, editor/agent integrations, configuration UX, process lifecycle, runtime supervision, integration orchestration, SDK |
| `oxide_kernel/crates/runtime` | Rust runtime: repository sessions, database/resource ownership, caches, provider clients, concurrency/scheduling, the Rust service boundary |
| `oxide_kernel/crates/kernel` | Rust kernel: repository intelligence and deterministic policy; no I/O, no dependencies on runtime, DB, transport or integration types |
| external runners (Ollama, llama.cpp, judge services) | model execution |

All Rust lives under `oxide_kernel/`; root `src/` is TypeScript only. Bun is the single JS/TS runtime, package manager and test runner; Mise is the developer entrypoint (`mise run verify`, which CI runs).

**Intended runtime model:** a long-lived `oxide-runtime` process, started and supervised by the Bun control plane, serving the versioned request/result/error contract across many requests. It owns LadybugDB, repository sessions, caches and provider clients for its lifetime. TS never opens the store or loads the kernel in-process.

**Phase 0 seam (provisional):** one `oxide-runtime` process per request, the request on stdin, one JSON response line on stdout. It exists to prove the typed contract end to end and is reversible: only `oxide_kernel/crates/runtime/src/main.rs` and `src/transport.ts` know about it, while the contract (`PROTOCOL_VERSION`, `oxide_kernel/contract/cases.json`, `src/contract.ts`) is transport-free. Integrations must not be built on the process-per-request shape.

## Consequences

- The kernel/runtime split is a crate boundary, enforced by `oxide_kernel/crates/kernel/tests/boundary.rs`; the TS boundary is enforced by `src/boundary.test.ts`.
- Resource ownership has one home (the runtime process), matching SPEC's single read/write store owner.
- Moving to the long-lived runtime changes the transport and lifecycle code, not the contract or kernel.
- No native addon, daemon socket or Deno/pnpm toolchain is part of v2.

## Open questions (resolve before real integrations)

- Framing over the long-lived channel (for example newline-delimited JSON over stdio vs a local socket), request multiplexing, and cancellation messages.
- Multi-client ownership: whether several CLI/editor/MCP clients share one runtime per repository, and how they discover it or receive an ownership-conflict error.
- Supervision policy: startup, health, restart/backoff, and shutdown/drain of in-flight work.
- Whether contract types should be generated from one schema once there are more than a few operations.
