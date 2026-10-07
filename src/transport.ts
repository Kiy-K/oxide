// Provisional Phase 0 transport (docs/adr/0003-kernel-runtime-control-plane-boundary.md):
// one `oxide-runtime` child process per request, JSON on stdin, one JSON line
// on stdout. Only this file knows that; the intended long-lived, Bun-supervised
// runtime replaces it without changing contract.ts or its callers.

import { decodeResponse, ProtocolError, type Request, type Response } from "./contract.ts";

/** The runtime could not be reached or did not answer in the contract. */
export class TransportError extends Error {
  override name = "TransportError";
}

/**
 * Sends raw bytes and returns the parsed (undecoded) reply. If `signal` aborts,
 * the child is killed and the promise rejects with `signal.reason`.
 */
export async function exchange(bin: string, raw: string, signal?: AbortSignal): Promise<unknown> {
  if (signal?.aborted) throw signal.reason;
  let child: Bun.Subprocess<Blob, "pipe", "pipe">;
  try {
    child = Bun.spawn([bin], { stdin: new Blob([raw]), stdout: "pipe", stderr: "pipe", signal });
  } catch (err) {
    throw new TransportError(`cannot start ${bin}: ${(err as Error).message}`, { cause: err });
  }
  const [stdout, stderr, code] = await Promise.all([
    new Response(child.stdout).text(),
    new Response(child.stderr).text(),
    child.exited,
  ]);
  // Cancellation is not a runtime failure: reject with the signal's reason
  // (an AbortError unless the caller chose one), never a TransportError.
  if (signal?.aborted) throw signal.reason;
  if (code !== 0) {
    const how = child.signalCode ? `killed by ${child.signalCode}` : `exited ${code}`;
    throw new TransportError(`oxide-runtime ${how}: ${stderr.trim()}`);
  }
  try {
    return JSON.parse(stdout);
  } catch (err) {
    throw new TransportError("oxide-runtime wrote non-JSON output", { cause: err });
  }
}

/** Sends one typed request and returns the decoded, id-checked response. */
export async function call(bin: string, request: Request, signal?: AbortSignal): Promise<Response> {
  const response = decodeResponse(await exchange(bin, JSON.stringify(request), signal));
  // A contract error whose id the runtime could not read (e.g. an oversized
  // request) carries `id: null`; it is still this request's answer.
  const unread = !response.ok && response.id === null;
  if (response.id !== request.id && !unread) {
    throw new ProtocolError(`response id ${response.id} does not match request ${request.id}`);
  }
  return response;
}
