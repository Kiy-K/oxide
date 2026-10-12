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
                src/transport.ts: Transport (process-per-request today)
                   │  src/contract.ts: wire protocol v1
                   ▼
                oxide-runtime ─▶ kernel
```

## Usage

```ts
import { createClient, OxideError } from "./src/index.ts";

await using oxide = createClient({ runtimeBin: "/path/to/oxide-runtime" });

const status = await oxide.getStatus({ signal: AbortSignal.timeout(5_000) });
// { apiVersion: 1, protocolVersion: 1, kernelVersion: "0.0.0", operations: ["getStatus"] }

if (status.operations.includes("buildContext")) {
  // not reachable with protocol 1
}

try {
  await oxide.openRepository("/work/repo");
} catch (err) {
  if (err instanceof OxideError && err.category === "capability") {
    // planned operation, not available from this runtime yet
  } else throw err;
}
```

`await using` (or an explicit `await oxide.close()`) releases the client.

## Operations

| Operation | Status | Backed by |
| --- | --- | --- |
| `getStatus()` | **Implemented** | protocol v1 `status` |
| `close()` | **Implemented** | client lifecycle |
| `openRepository(root)` | Contract only: rejects with `capability` / `unsupported_operation` | nothing yet |
| `indexRepository(repository)` | Contract only | nothing yet |
| `search(repository, request)` | Contract only | nothing yet |
| `buildContext(repository, request)` | Contract only | nothing yet |

Contract-only operations never contact the runtime and never return
fabricated data. Their result types (`RepositoryHandle`, `SourceLocation`,
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
| `transport` | runtime not reached / no answer (spawn failure, crash, non-JSON) | `runtime_unavailable`, `transport_failed` |
| `protocol` | answer outside the contract, wrong id, or version mismatch | `malformed_response`, `unsupported_version`, `invalid_request` |
| `capability` | operation not supported by this client/runtime | `unsupported_operation` |
| `operation` | a supported operation ran and failed | none in protocol 1 (reserved) |
| `closed` | client closed before or during the call | `client_closed` |

The cause (`TransportError`, `ProtocolError`) is kept on `error.cause` for
debugging. It is not part of the API.

## Cancellation and lifecycle

- Every operation takes `{ signal?: AbortSignal }`. An aborted call rejects
  with `signal.reason` (an `AbortError` unless you chose a reason), never with
  an `OxideError`. The process transport kills the runtime child. A result
  that arrives after the abort is discarded.
- The client owns its transport. `close()` is idempotent: it rejects in-flight
  calls with `closed`, makes later calls reject with `closed`, and releases
  the transport exactly once.
- Calls may overlap, but each is independent. There are no sessions, retries,
  reconnection or recovery, and the API doesn't promise any.

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
  over the supervised process and pass it to `clientOver`. `createClient`
  switches to it. The public API is unchanged.
- **New operations in the protocol:** add the wire op and decoder in
  `contract.ts` (with shared cases), map the public request/result in the
  adapter, and add the operation to `SUPPORTED`. Once the runtime reports
  capabilities, `SUPPORTED` becomes the runtime's answer instead of a
  constant.
- **Rust core rewrite:** only the runtime side of the contract changes. If
  the wire contract changes too, the adapter maps the new protocol onto the
  same public types.

## Limits of the current runtime protocol

- Protocol 1 has a single operation, `status`. There is no repository,
  index, search or context op, and no capability negotiation.
- Transport is provisional: one `oxide-runtime` process per request, so no
  shared state, caches or sessions. Each call pays a process spawn.
- No runtime discovery: `runtimeBin` is required.
- No `operation`-category errors exist on the wire yet.
