// OXIDE v2 service contract, version 1 (oxide_kernel/README.md § Service contract).
// Types plus a strict decoder: TS validates the envelope it receives and
// never interprets repository meaning. The shared cases in
// oxide_kernel/contract/cases.json pin the wire shape for both languages.

export const PROTOCOL_VERSION = 1;

export type Operation = "status";

export interface Request {
  version: typeof PROTOCOL_VERSION;
  id: string;
  op: Operation;
}

export type ErrorCode = "invalid_request" | "unsupported_version" | "unknown_operation";

export interface StatusResult {
  kernel_version: string;
}

export type Response =
  | { version: typeof PROTOCOL_VERSION; id: string; ok: true; result: StatusResult }
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
] satisfies ErrorCode[];

/** The runtime's reply was not a valid version 1 response. */
export class ProtocolError extends Error {
  override name = "ProtocolError";
}

type Json = Record<string, unknown>;

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

/** Strictly decodes one parsed response; throws ProtocolError otherwise. */
export function decodeResponse(value: unknown): Response {
  const head = object(value, Object.keys(value ?? {}), "response");
  if (head.version !== PROTOCOL_VERSION) {
    throw new ProtocolError(`runtime speaks protocol ${String(head.version)}`);
  }
  if (head.ok === true) {
    const r = object(value, ["version", "id", "ok", "result"], "response");
    const result = object(r.result, ["kernel_version"], "result");
    return {
      version: PROTOCOL_VERSION,
      id: string(r.id, "id"),
      ok: true,
      result: { kernel_version: string(result.kernel_version, "result.kernel_version") },
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
