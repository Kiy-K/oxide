// OXIDE public TypeScript API (docs/public-api.md): domain types and one
// OxideClient. The wire contract (contract.ts) and the runtime supervisor
// (transport.ts) stay internal; a new protocol version or transport changes
// only the adapter section at the bottom of this file.
// Nothing here interprets repository meaning: results come from Rust or the
// call fails.

import {
  type CapabilitiesResult,
  checkCorrelation,
  decodeCapabilities,
  decodeClosed,
  decodeOpened,
  decodeStatus,
  type Json,
  PROTOCOL_VERSION,
  ProtocolError,
  type Request,
  type Response,
} from "./contract.ts";
import { RuntimeSupervisor, type Transport, TransportError } from "./transport.ts";

/** Public API version; independent of the runtime protocol version. */
export const API_VERSION = 1;

/** Public operations. New members may be added within API version 1. */
export type Operation =
  | "getStatus"
  | "openRepository"
  | "closeRepository"
  | "indexRepository"
  | "search"
  | "buildContext";

export interface Status {
  apiVersion: number;
  /** The runtime protocol this client spoke to get the answer. */
  protocolVersion: number;
  kernelVersion: string;
  /** Operations this client and runtime can perform; others fail as `capability`. */
  operations: readonly Operation[];
}

/**
 * An open repository session in this client's runtime. `id` is opaque and
 * valid only until the repository or the client is closed; never parse it.
 */
export interface RepositoryHandle {
  readonly id: string;
  /** Canonical absolute checkout path. */
  readonly root: string;
  /** Opaque identity of the published snapshot this session reads. */
  readonly snapshot: string;
}

// Contract-only types: the planned operations below have no runtime support
// yet and always fail. These shapes are provisional until the runtime backs
// them (docs/public-api.md § Compatibility).

/** Evidence in one captured file: repository-relative path, byte range, digest. */
export interface SourceLocation {
  path: string;
  /** Half-open byte range of the captured bytes. */
  bytes: { start: number; end: number };
  /** 1-based first and last line, derived from `bytes`. */
  lines: { first: number; last: number };
  /** Digest of the captured file the bytes were verified against. */
  digest: string;
}

/** Why a result is incomplete or degraded. Branch on `code`; `message` is for humans. */
export interface Diagnostic {
  code: string;
  message: string;
}

export interface IndexResult {
  repository: RepositoryHandle;
  /** Opaque identity of the published snapshot. */
  snapshot: string;
  diagnostics: Diagnostic[];
}

export interface SearchRequest {
  query: string;
  limit?: number;
}

export interface SearchHit {
  location: SourceLocation;
  /** Declared name of the matched entity, when it has one. */
  name: string | null;
}

export interface SearchResult {
  snapshot: string;
  hits: SearchHit[];
  diagnostics: Diagnostic[];
}

/** Payload-token allowance for one bundle. */
export interface ContextBudget {
  tokens: number;
}

export interface ContextRequest {
  query: string;
  budget: ContextBudget;
  /** Explicit hints; Rust validates and resolves them. */
  hints?: { paths?: string[]; symbols?: string[]; changed?: string[] };
}

export interface ContextItem {
  location: SourceLocation;
  tokens: number;
  text: string;
}

export interface ContextBundle {
  snapshot: string;
  budget: ContextBudget;
  usedTokens: number;
  /** The counting contract `tokens` are measured under; `strict: false` means estimated. */
  tokenCounter: { name: string; strict: boolean };
  items: ContextItem[];
  /** The agent-facing text: every item's `text`, in order. */
  payload: string;
  omitted: Diagnostic[];
  diagnostics: Diagnostic[];
}

/**
 * - `transport`: the runtime could not be reached or did not answer.
 * - `protocol`: the runtime answered outside the contract, or speaks another version.
 * - `capability`: this client or runtime does not support the operation.
 * - `operation`: the runtime ran a supported operation and it failed.
 * - `closed`: the client was closed before or during the call.
 *
 * No new categories within API version 1. Codes may be added; branch on
 * `category` first.
 */
export type ErrorCategory = "transport" | "protocol" | "capability" | "operation" | "closed";

export class OxideError extends Error {
  override name = "OxideError";
  readonly category: ErrorCategory;
  readonly code: string;
  /** Id of the runtime request this error belongs to; null if none was sent. */
  readonly requestId: string | null;

  constructor(
    category: ErrorCategory,
    code: string,
    message: string,
    requestId: string | null,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.category = category;
    this.code = code;
    this.requestId = requestId;
  }
}

/** Per-call options. An aborted call rejects with `signal.reason`. */
export interface CallOptions {
  signal?: AbortSignal;
}

/**
 * Owns one runtime process until `close()`. Calls may overlap; the runtime
 * runs them one at a time, in order. Each is independent (no sessions, no
 * retries); if the runtime exits, the client fails every later call.
 */
export interface OxideClient {
  getStatus(options?: CallOptions): Promise<Status>;
  /**
   * Opens an already indexed repository at the absolute path `root`, pinning
   * its current published snapshot; never indexes. Opening the same
   * repository again in this client returns the same handle. Fails with
   * `operation` (`invalid_path`, `not_indexed`, `repository_unavailable`,
   * `incompatible_derivation`, `ownership_conflict`, `internal_error`).
   */
  openRepository(root: string, options?: CallOptions): Promise<RepositoryHandle>;
  /**
   * Releases the session and its store lock, so other processes can own the
   * repository. A closed or foreign handle fails with `operation`/`unknown_handle`.
   */
  closeRepository(repository: RepositoryHandle, options?: CallOptions): Promise<void>;
  /** Planned; fails with `capability` today. */
  indexRepository(repository: RepositoryHandle, options?: CallOptions): Promise<IndexResult>;
  /** Planned; fails with `capability` today. */
  search(
    repository: RepositoryHandle,
    request: SearchRequest,
    options?: CallOptions,
  ): Promise<SearchResult>;
  /** Planned; fails with `capability` today. */
  buildContext(
    repository: RepositoryHandle,
    request: ContextRequest,
    options?: CallOptions,
  ): Promise<ContextBundle>;
  /**
   * Idempotent. In-flight calls reject with `closed`, later calls too, and the
   * transport is released once.
   */
  close(): Promise<void>;
  [Symbol.asyncDispose](): Promise<void>;
}

export interface ClientOptions {
  /** Path to the `oxide-runtime` binary. There is no discovery yet. */
  runtimeBin: string;
}

/** The runtime process starts on the first call, not here. */
export function createClient(options: ClientOptions): OxideClient {
  return clientOver(new RuntimeSupervisor([options.runtimeBin]));
}

// Internal adapter: public operations onto protocol version 1. Not exported
// from index.ts.

/**
 * The wire operation behind each implemented public operation. A public
 * operation is reported only if the runtime's capabilities list it; ones
 * absent here have no adapter and are never reported.
 */
const WIRE: Partial<Record<Operation, Request["op"]>> = {
  getStatus: "status",
  openRepository: "open_repository",
  closeRepository: "close_repository",
};

/** A client over any transport (tests, or a replacement for the supervisor). */
export function clientOver(transport: Transport): OxideClient {
  const lifetime = new AbortController();
  let closing: Promise<void> | undefined;
  /** Fixed for the runtime's lifetime, so fetched once. */
  let capabilities: CapabilitiesResult | undefined;

  const closed = (requestId: string | null) =>
    new OxideError("closed", "client_closed", "the client is closed", requestId);

  const unsupported = async (op: Operation, options?: CallOptions): Promise<never> => {
    if (lifetime.signal.aborted) throw closed(null);
    options?.signal?.throwIfAborted();
    throw new OxideError(
      "capability",
      "unsupported_operation",
      `${op} is not implemented by this client and runtime`,
      null,
    );
  };

  async function send<T>(
    op: Request["op"],
    decode: (result: Json) => T,
    signal?: AbortSignal,
    params?: Json,
  ) {
    if (lifetime.signal.aborted) throw closed(null);
    signal?.throwIfAborted();
    const request: Request = { version: PROTOCOL_VERSION, id: crypto.randomUUID(), op };
    if (params) request.params = params;
    const both = signal ? AbortSignal.any([signal, lifetime.signal]) : lifetime.signal;
    let response: Response;
    let result: T | undefined;
    try {
      response = await transport.send(request, both);
      // A transport that ignored the signal still must not deliver a result.
      both.throwIfAborted();
      checkCorrelation(request, response);
      if (response.ok) result = decode(response.result);
    } catch (err) {
      if (lifetime.signal.aborted && err === lifetime.signal.reason) throw closed(request.id);
      if (signal?.aborted && err === signal.reason) throw err;
      throw fromFailure(err, request.id);
    }
    if (!response.ok) throw fromContractError(response.error, request.id);
    return result as T;
  }

  /** The runtime's capabilities, fetched once. */
  async function runtime(signal?: AbortSignal): Promise<CapabilitiesResult> {
    capabilities ??= await send("capabilities", decodeCapabilities, signal);
    return capabilities;
  }

  /** Public operations both this adapter and the runtime implement. */
  const available = (caps: CapabilitiesResult) =>
    (Object.keys(WIRE) as Operation[]).filter((op) => caps.operations.includes(WIRE[op] ?? ""));

  /** Rejects `op` as `capability` unless the runtime advertises it. */
  async function ensure(op: Operation, signal?: AbortSignal): Promise<void> {
    if (!available(await runtime(signal)).includes(op)) await unsupported(op, { signal });
  }

  return {
    async getStatus(options) {
      const caps = await runtime(options?.signal);
      const status = await send("status", decodeStatus, options?.signal);
      return {
        apiVersion: API_VERSION,
        protocolVersion: caps.protocol_version,
        kernelVersion: status.kernel_version,
        operations: available(caps),
      };
    },
    async openRepository(root, options) {
      await ensure("openRepository", options?.signal);
      const opened = await send("open_repository", decodeOpened, options?.signal, { path: root });
      return { id: opened.handle, root: opened.root, snapshot: opened.snapshot };
    },
    async closeRepository(repository, options) {
      await ensure("closeRepository", options?.signal);
      await send("close_repository", decodeClosed, options?.signal, { handle: repository.id });
    },
    indexRepository: (_repository, options) => unsupported("indexRepository", options),
    search: (_repository, _request, options) => unsupported("search", options),
    buildContext: (_repository, _request, options) => unsupported("buildContext", options),
    close() {
      closing ??= (async () => {
        lifetime.abort(closed(null));
        try {
          await transport.close();
        } catch (err) {
          throw fromFailure(err, null);
        }
      })();
      return closing;
    },
    [Symbol.asyncDispose]() {
      return this.close();
    },
  };
}

function fromFailure(err: unknown, requestId: string | null): OxideError {
  const message = err instanceof Error ? err.message : String(err);
  if (err instanceof ProtocolError) {
    return new OxideError("protocol", "malformed_response", message, requestId, { cause: err });
  }
  // TransportError or anything else a transport throws: the runtime was not reached.
  const code = err instanceof TransportError ? "runtime_unavailable" : "transport_failed";
  return new OxideError("transport", code, message, requestId, { cause: err });
}

function fromContractError(
  error: Extract<Response, { ok: false }>["error"],
  requestId: string,
): OxideError {
  switch (error.code) {
    case "unknown_operation":
      return new OxideError("capability", "unsupported_operation", error.message, requestId);
    case "unsupported_version":
      return new OxideError("protocol", "unsupported_version", error.message, requestId);
    case "invalid_request":
      return new OxideError("protocol", "invalid_request", error.message, requestId);
    default:
      // A supported operation ran and failed: the runtime's code is public.
      return new OxideError("operation", error.code, error.message, requestId);
  }
}
