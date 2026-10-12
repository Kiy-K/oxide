// OXIDE v2 service contract, version 1 (oxide_kernel/README.md § Service contract).
// Types plus strict decoders: TS validates the envelope it receives, then the
// result of the operation it asked for, and never interprets repository
// meaning. The shared cases in
// oxide_kernel/contract/cases.json pin the wire shape for both languages.

export const PROTOCOL_VERSION = 1;

export type Operation = "status" | "capabilities" | "open_repository" | "close_repository";

export interface Request {
  version: typeof PROTOCOL_VERSION;
  id: string;
  op: Operation;
  /** Required by operations that take parameters, absent otherwise. */
  params?: Json;
}

/** Envelope errors, then operation failures (only from operations with params). */
export type ErrorCode =
  | "invalid_request"
  | "unsupported_version"
  | "unknown_operation"
  | OperationErrorCode;

export type OperationErrorCode =
  | "invalid_path"
  | "not_indexed"
  | "repository_unavailable"
  | "incompatible_derivation"
  | "ownership_conflict"
  | "unknown_handle"
  | "internal_error";

export const OPERATION_ERROR_CODES: readonly OperationErrorCode[] = [
  "invalid_path",
  "not_indexed",
  "repository_unavailable",
  "incompatible_derivation",
  "ownership_conflict",
  "unknown_handle",
  "internal_error",
];

export interface StatusResult {
  kernel_version: string;
}

/** An open repository session in this runtime. */
export interface OpenedResult {
  /** Runtime-scoped session handle. */
  handle: string;
  repo_id: string;
  /** Canonical checkout path. */
  root: string;
  /** The pinned published snapshot and its derivation. */
  snapshot: string;
  derivation: string;
}

export interface CapabilitiesResult {
  protocol_version: number;
  /** Wire operations the runtime answers; may include ones this client does not know. */
  operations: string[];
  max_request_bytes: number;
  /** `serial`: one request at a time, answered in arrival order. */
  execution: string;
}

/** `result` is a JSON object; decode it with the decoder for the operation sent. */
export type Response =
  | { version: typeof PROTOCOL_VERSION; id: string; ok: true; result: Json }
  | {
      version: typeof PROTOCOL_VERSION;
      id: string | null;
      ok: false;
      error: { code: ErrorCode; message: string };
    };

const ERROR_CODES: readonly string[] = [
  "invalid_request",
  "unsupported_version",
  "unknown_operation",
  ...OPERATION_ERROR_CODES,
] satisfies ErrorCode[];

/** The runtime's reply was not a valid version 1 response. */
export class ProtocolError extends Error {
  override name = "ProtocolError";
}

export type Json = Record<string, unknown>;

function object(value: unknown, keys: string[], where: string): Json {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ProtocolError(`${where} is not an object`);
  }
  const actual = Object.keys(value).sort();
  if (actual.join() !== [...keys].sort().join()) {
    throw new ProtocolError(`${where} has keys [${actual}], expected [${keys}]`);
  }
  return value as Json;
}

function string(value: unknown, where: string): string {
  if (typeof value !== "string") throw new ProtocolError(`${where} is not a string`);
  return value;
}

/**
 * Throws ProtocolError unless `response` answers `request`. A contract error
 * whose id the runtime could not read (e.g. an oversized request) carries
 * `id: null`; it is still this request's answer.
 */
export function checkCorrelation(request: Request, response: Response): void {
  const unread = !response.ok && response.id === null;
  if (response.id !== request.id && !unread) {
    throw new ProtocolError(`response id ${response.id} does not match request ${request.id}`);
  }
}

/** Strictly decodes one parsed response; throws ProtocolError otherwise. */
export function decodeResponse(value: unknown): Response {
  const head = object(value, Object.keys(value ?? {}), "response");
  if (head.version !== PROTOCOL_VERSION) {
    throw new ProtocolError(`runtime speaks protocol ${String(head.version)}`);
  }
  if (head.ok === true) {
    const r = object(value, ["version", "id", "ok", "result"], "response");
    return {
      version: PROTOCOL_VERSION,
      id: string(r.id, "id"),
      ok: true,
      result: object(r.result, Object.keys(r.result ?? {}), "result"),
    };
  }
  if (head.ok === false) {
    const r = object(value, ["version", "id", "ok", "error"], "response");
    const error = object(r.error, ["code", "message"], "error");
    const code = string(error.code, "error.code");
    if (!ERROR_CODES.includes(code)) throw new ProtocolError(`unknown error code ${code}`);
    return {
      version: PROTOCOL_VERSION,
      id: r.id === null ? null : string(r.id, "id"),
      ok: false,
      error: { code: code as ErrorCode, message: string(error.message, "error.message") },
    };
  }
  throw new ProtocolError("response.ok is not a boolean");
}

export function decodeStatus(result: Json): StatusResult {
  const r = object(result, ["kernel_version"], "status result");
  return { kernel_version: string(r.kernel_version, "kernel_version") };
}

export function decodeCapabilities(result: Json): CapabilitiesResult {
  const r = object(
    result,
    ["protocol_version", "operations", "max_request_bytes", "execution"],
    "capabilities result",
  );
  if (!Array.isArray(r.operations)) throw new ProtocolError("operations is not an array");
  return {
    protocol_version: integer(r.protocol_version, "protocol_version"),
    operations: r.operations.map((op, i) => string(op, `operations[${i}]`)),
    max_request_bytes: integer(r.max_request_bytes, "max_request_bytes"),
    execution: string(r.execution, "execution"),
  };
}

function integer(value: unknown, where: string): number {
  if (!Number.isSafeInteger(value)) throw new ProtocolError(`${where} is not an integer`);
  return value as number;
}

export function decodeOpened(result: Json): OpenedResult {
  const r = object(
    result,
    ["handle", "repo_id", "root", "snapshot", "derivation"],
    "open_repository result",
  );
  return {
    handle: string(r.handle, "handle"),
    repo_id: string(r.repo_id, "repo_id"),
    root: string(r.root, "root"),
    snapshot: string(r.snapshot, "snapshot"),
    derivation: string(r.derivation, "derivation"),
  };
}

/** `close_repository` answers an empty object. */
export function decodeClosed(result: Json): void {
  object(result, [], "close_repository result");
}
