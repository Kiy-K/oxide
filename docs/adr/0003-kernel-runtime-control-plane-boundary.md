# ADR-0003: Rust kernel/runtime and Bun control-plane boundary

## Status

**Proposed** — 2026-10-07; amended 2026-10-12 (persistent transport, capabilities, ownership plan), still Proposed. Follow-up to [ADR-0001](0001-oxide-v2-rewrite.md) (also Proposed), listed there as "Rust kernel/runtime and TypeScript control-plane boundary". Records the layout, the runtime model and the current local transport. The framing below is the implemented choice, reversible behind `src/transport.ts`; it is not frozen.

**Superseded** — 2026-10-15 by [ADR-0011](0011-python-engine-vectorless.md): OXIDE v2 is one Python process with no runtime child, transport or TypeScript control plane. The record below is kept as written.

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

**Phase 0 seam (superseded 2026-10-12):** one `oxide-runtime` process per request. It proved the typed contract end to end and was replaced by the transport below without changing the contract or the public TypeScript API.

### Transport and lifecycle (2026-10-12)

- **Framing:** newline-delimited JSON over the child's stdin/stdout. One request per line, one response per line, flushed, in arrival order. Diagnostics go to stderr only. JSON encoders never emit a raw newline, so a line is a frame. A local socket was not needed: one supervisor owns one child, and stdio gives EOF-on-parent-death for free. Revisit when a runtime must be shared across processes (below).
- **Bounds:** request lines over `MAX_REQUEST_BYTES` (1 MiB) are discarded without buffering and answered with a null-id `invalid_request`. The stream continues after any malformed line. On the TS side, response lines are bounded (16 Mi UTF-16 units), at most 64 requests wait, and more are rejected rather than buffered.
- **Execution:** the runtime is serial (`execution: "serial"` in `capabilities`), and the supervisor keeps exactly one request on the wire. This makes null-id errors unambiguous and lets queued requests be cancelled before they're sent. Concurrency inside the runtime is a later, measured change, reported through `capabilities`.
- **Supervision (`src/transport.ts`, `RuntimeSupervisor`):** the first call spawns the child, and later calls reuse it. Correlation is by request id. Stdout contamination, a bad frame or an uncorrelated answer is a protocol error: the supervisor SIGKILLs its own child. An exit or crash rejects every pending call. The transport then stays failed: **no automatic restart and no retry**, because a retried call could repeat a state-changing operation. A new client starts a new runtime. The child is unref'd while idle, so a forgotten client doesn't keep Bun alive.
- **Cancellation:** abort before sending, or while queued, means the request is never sent. Abort while active releases the caller at once. The runtime still finishes that work (there is no cancel message), its answer is dropped, and the next request waits for it. Interrupting running Rust work needs a cancel message and cooperative checks in the runtime; that is deferred until an operation runs long enough to matter.
- **Shutdown:** `close()` rejects queued work and closes stdin. The runtime finishes its current request, sees EOF and exits 0. After a 2 s grace period the supervisor SIGKILLs its child; it never signals any process it didn't spawn.
- **Capabilities:** the `capabilities` operation reports `protocol_version`, `operations` (exactly what `handle` answers), `max_request_bytes` and `execution`. Adding an operation is backward-compatible, so protocol 1 was not bumped. The TS adapter reports a public operation only when it has an adapter for it **and** the runtime lists the wire operation. No backend availability is reported yet: nothing per-runtime is configured or reachable through the protocol, and reporting more would be guesswork.

### Resource ownership plan (partly implemented)

`open_repository`/`close_repository` (2026-10-12) implement the first part: sessions own their store and its lock, repeated opens share one session, and a second runtime gets `ownership_conflict`. Everything else below still constrains later operations.

- **Lifetime:** the runtime process owns every repository session. A session is an opened repository bound to a published snapshot's read view. A session holds its LadybugDB `Database` owner (one per store) and the connections, caches and provider clients created from it. A session ends on an explicit close or at runtime exit. On EOF the runtime finishes the current request, then drops sessions (connections before the owner) and exits.
- **Concurrent callers in one client:** they share the one runtime, serialized today. Later, reads may run concurrently on connections from the single owner (LadybugDB permits this). Publication stays serialized by the adapter's writer queue. Any change is advertised via `execution`.
- **Independent Bun processes:** each spawns its own runtime. A store has exactly one read/write owner: the adapter takes an OS file lock (`store.lock`, `try_lock`), and a second runtime gets `OwnershipConflict`. That conflict must surface as a typed contract error, never a wait or a second writer. Sharing one runtime between processes (discovery, a socket, attach/detach) is **not solved** by this design.
- **Exclusivity:** exclusive per store per runtime process, shared by all sessions and callers inside that runtime. TypeScript never opens the store.
- **Crash and recovery:** a crash releases the OS lock. The publication protocol (ADR-0002) keeps a partially staged generation invisible, so the next runtime reopens the last published generation. The control plane reports the crash; it doesn't replay the request.
- **Shutdown with pending work:** queued work is rejected. A long active operation (indexing) would be SIGKILLed after the grace period. That is safe for publication but wastes the work, so a drain timeout and a cancel message are prerequisites for exposing indexing over IPC.

## Consequences

- The kernel/runtime split is a crate boundary, enforced by `oxide_kernel/crates/kernel/tests/boundary.rs`; the TS boundary is enforced by `src/boundary.test.ts`.
- Resource ownership has one home (the runtime process), matching SPEC's single read/write store owner.
- Moving to the long-lived runtime changes the transport and lifecycle code, not the contract or kernel.
- No native addon, daemon socket or Deno/pnpm toolchain is part of v2.

## Open questions (resolve before real integrations)

- Cancellation messages and cooperative cancellation inside long runtime operations; concurrent execution and request multiplexing within one runtime.
- Multi-client ownership: whether several CLI/editor/MCP processes share one runtime per repository (and how they discover it), or each runs its own and receives a typed ownership-conflict error.
- Supervision policy beyond "fail and report": health checks, restart/backoff for idempotent reads, drain timeouts for long writes, and startup cost measurements.
- Whether contract types should be generated from one schema once there are more than a few operations.
