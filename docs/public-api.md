# OXIDE public TypeScript API

The public API is what root `src/index.ts` exports: `createClient`,
`OxideClient`, `OxideError`, `API_VERSION` and plain domain types. Code is in
`src/client.ts`; tests are in `src/client.test.ts`. Integrations (CLI, MCP,
SDK users) depend on this module only, never on `contract.ts` or
`transport.ts`.

```text
integrations ─▶ src/index.ts (public API, API_VERSION 1)
                   │  src/client.ts: OxideClient + internal adapter
                   ▼
                src/transport.ts: Transport = RuntimeSupervisor (one long-lived child)
                   │  src/contract.ts: wire protocol v1
                   ▼
                oxide-runtime ─▶ kernel
```

## Usage

```ts
import { createClient, OxideError } from "./src/index.ts";

await using oxide = createClient({ runtimeBin: "/path/to/oxide-runtime" });

const status = await oxide.getStatus({ signal: AbortSignal.timeout(5_000) });
// { apiVersion: 1, protocolVersion: 1, kernelVersion: "0.0.0",
//   operations: ["getStatus", "openRepository", "closeRepository"] }

try {
  const repo = await oxide.openRepository("/work/repo"); // absolute, already indexed
  // { id: "<32 hex>", root: "/work/repo", snapshot: "sha256:..." }
  await oxide.closeRepository(repo); // optional: releases the store lock now
} catch (err) {
  if (err instanceof OxideError && err.category === "operation") {
    // err.code: invalid_path | not_indexed | repository_unavailable |
    // incompatible_derivation | ownership_conflict | unknown_handle | internal_error
  } else throw err;
}
```

`await using` (or an explicit `await oxide.close()`) releases the client and
every repository it still has open.

## Operations

| Operation | Status | Backed by |
| --- | --- | --- |
| `getStatus()` | **Implemented** | protocol v1 `status` |
| `close()` | **Implemented** | client lifecycle |
| `openRepository(root)` | **Implemented** | `open_repository`: registry lookup + LadybugDB store |
| `closeRepository(repository)` | **Implemented** | `close_repository` |
| `indexRepository(repository)` | Contract only | nothing yet |
| `search(repository, request)` | Contract only | nothing yet |
| `buildContext(repository, request)` | Contract only | nothing yet |

Contract-only operations never contact the runtime and never return
fabricated data. Their result types (`SourceLocation`,
`SearchResult`, `ContextBundle`, `Diagnostic`, ...) mirror the kernel's domain
concepts (`SourceRef`, `ContextBudget`, `ContextItem`, `ContextBundle`) and
expose none of its internals. `Status.operations` is the capability report:
check it before calling an operation.

## Errors

Every failure that isn't a cancellation is an `OxideError` with `category`,
`code`, `message` (for humans) and `requestId`. `requestId` is the wire
request id, or `null` when no request was sent.

| Category | Meaning | Codes today |
| --- | --- | --- |
| `transport` | runtime not reached / no answer (spawn failure, exit, crash, full queue) | `runtime_unavailable`, `transport_failed` |
| `protocol` | answer outside the contract, wrong id, or version mismatch | `malformed_response`, `unsupported_version`, `invalid_request` |
| `capability` | operation not supported by this client/runtime | `unsupported_operation` |
| `operation` | a supported operation ran and failed | the runtime's code, see below |
| `closed` | client closed before or during the call | `client_closed` |

The cause (`TransportError`, `ProtocolError`) is kept on `error.cause` for
debugging. It is not part of the API.

`operation` codes (from the runtime, unchanged):

| Code | When |
| --- | --- |
| `invalid_path` | `root` is not absolute, doesn't resolve to a directory, or contains the data location |
| `not_indexed` | not registered, or registered with no published generation (never indexed, or only in an older storage format) |
| `repository_unavailable` | stored state is corrupt; rebuild required |
| `incompatible_derivation` | published under another derivation, storage format or encoding than this runtime's |
| `ownership_conflict` | another runtime process (another client) has the repository open |
| `unknown_handle` | the handle was closed, forged, or came from another client/runtime |
| `internal_error` | the runtime failed for reasons not caused by the request |

## Repositories

- `openRepository(root)` opens an **already indexed** repository. It never
  registers, captures or indexes. The registry under `$XDG_DATA_HOME/oxide/v2`
  (or `~/.local/share/oxide/v2`) maps the canonical checkout path to its
  store. The handle pins the snapshot that was current when it opened, under
  this runtime's own derivation. Whether the working tree has changed since
  indexing is not checked.
- Handles are scoped to one client's runtime: an unguessable 128-bit id that
  is never reused or accepted by another runtime. Opening the same repository
  again (by any path spelling) returns the **same handle**.
- An open repository holds its store's exclusive lock. Another client (another
  runtime process) gets `ownership_conflict` until `closeRepository`,
  `close()`, or the runtime's exit releases it; a crash releases it too. There
  is no sharing across processes.
- An `openRepository` aborted while the runtime runs it may still open the
  session. Calling it again returns that handle, and `close()` releases it
  either way.

## Cancellation and lifecycle

- Every operation takes `{ signal?: AbortSignal }`. An aborted call rejects
  with `signal.reason` (an `AbortError` unless you chose a reason), never with
  an `OxideError`. A call still waiting in the client's queue is never sent.
  A call the runtime is already running is not interrupted: the runtime
  finishes it, its answer is discarded, and later calls wait for it.
- The client owns its transport. `close()` is idempotent: it rejects in-flight
  calls with `closed`, makes later calls reject with `closed`, and releases
  the transport exactly once.
- Calls may overlap; the runtime runs them one at a time, in order. Each is
  independent. There are no sessions, retries, reconnection or recovery, and
  the API doesn't promise any. If the runtime exits, every pending and later
  call fails with `transport`.
- The runtime process starts on the first call and is the client's alone. A
  client that is never closed does not keep Bun alive while idle; when Bun
  exits, the runtime sees EOF and exits too.

## Compatibility

- **Two versions, separate.** `API_VERSION` (public, here) and
  `PROTOCOL_VERSION` (wire, `contract.ts`) change independently. A protocol
  bump, a new transport or a rewritten Rust core is absorbed by the adapter
  section of `client.ts` and by `transport.ts`. It changes neither
  `API_VERSION` nor any exported type.
- **Stable within API version 1:** `getStatus`/`Status`, `close`,
  `OxideError` and its five categories, the cancellation rule, and the
  `close` semantics. Additive changes may happen without a bump: new
  `Operation` members, new error codes, new optional fields. Removing or
  changing any of these bumps `API_VERSION`.
- **Provisional:** contract-only operations and their request/result types.
  They may change until the runtime backs them; the first backed release
  freezes them. Until then they only reject with `capability`.
- Wire types, `Transport`, `clientOver`, `call`/`exchange` and the error
  classes in `contract.ts`/`transport.ts` are internal and change without
  notice.

### Absorbing runtime changes

- **Long-lived runtime (ADR-0003):** implement `Transport` (`send`, `close`)
  (done): `RuntimeSupervisor` implements `Transport` (`send`, `close`) over one
  supervised process; `createClient` uses it. The public API did not change.
- **New operations in the protocol:** add the wire op and decoder in
  `contract.ts` (with shared cases), map the public request/result in the
  adapter's `WIRE` table. `Status.operations` lists a public operation only
  if it has an adapter and the runtime's `capabilities` report its wire
  operation, so nothing is advertised before both sides implement it.
- **Rust core rewrite:** only the runtime side of the contract changes. If
  the wire contract changes too, the adapter maps the new protocol onto the
  same public types.

## Limits of the current runtime protocol

- Protocol 1 has `status`, the internal `capabilities`, and
  `open_repository`/`close_repository`. There is no index, search or context
  operation, so an opened handle can't be used for anything else yet.
- One runtime process per client, serial execution, 64 queued calls at most.
  No cancel message, so running work can't be interrupted. No restart: after
  the runtime exits, create a new client. Two clients mean two runtimes;
  nothing coordinates them yet (ADR-0003).
- No runtime discovery: `runtimeBin` is required.
