// Runtime supervisor (docs/adr/0003-kernel-runtime-control-plane-boundary.md):
// one long-lived `oxide-runtime` child per transport, newline-delimited JSON
// on its stdin/stdout, diagnostics on its stderr. Only this file knows about
// processes and framing; contract.ts and the client do not.

import {
  checkCorrelation,
  decodeResponse,
  ProtocolError,
  type Request,
  type Response,
} from "./contract.ts";

/** The runtime could not be reached, exited, or the transport is closed. */
export class TransportError extends Error {
  override name = "TransportError";
}

/**
 * The replaceable seam under the public client (src/client.ts): one decoded,
 * correlated response per request. If `signal` aborts, `send` rejects with
 * its reason.
 */
export interface Transport {
  send(request: Request, signal?: AbortSignal): Promise<Response>;
  /** Releases what the transport owns; the client calls it once, on close. */
  close(): Promise<void>;
}

/** Requests waiting behind the active one; more are rejected, not buffered. */
export const MAX_QUEUED = 64;
/** Longest response line accepted (UTF-16 code units); longer is a protocol violation. */
export const MAX_RESPONSE_CHARS = 16 << 20;
/** How long close() waits for a graceful exit before SIGKILL. */
export const CLOSE_GRACE_MS = 2_000;
const STDERR_TAIL_CHARS = 4_096;

interface Pending {
  request: Request;
  resolve: (response: Response) => void;
  reject: (reason: unknown) => void;
  signal?: AbortSignal;
  onAbort?: () => void;
  /** The caller gave up while Rust was running it; drop its answer. */
  abandoned?: boolean;
}

/**
 * Supervises one runtime process, started by the first `send` and reused
 * until it exits or `close()`. One request is on the wire at a time (the
 * runtime executes serially); up to MAX_QUEUED wait in order behind it.
 *
 * Cancellation: a queued request is removed and never sent. An active
 * request's caller is released at once, but the runtime finishes that work
 * (there is no cancel message) and its answer is discarded; the next request
 * waits for it. If the process exits, every pending call rejects with a
 * TransportError and the transport stays failed: nothing is retried or
 * restarted.
 */
export class RuntimeSupervisor implements Transport {
  readonly #argv: string[];
  #child: Bun.Subprocess<"pipe", "pipe", "pipe"> | undefined;
  /** Set once: every later send rejects with it. */
  #failure: Error | undefined;
  #active: Pending | undefined;
  #queue: Pending[] = [];
  #stderr = "";
  #finished: Promise<void> = Promise.resolve();
  #closing: Promise<void> | undefined;

  constructor(argv: string[]) {
    this.#argv = argv;
  }

  /** The child's PID once started (tests and diagnostics). */
  get pid(): number | undefined {
    return this.#child?.pid;
  }

  send(request: Request, signal?: AbortSignal): Promise<Response> {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) return reject(signal.reason);
      if (this.#failure) return reject(this.#failure);
      if (this.#queue.length >= MAX_QUEUED) {
        return reject(new TransportError(`runtime queue is full (${MAX_QUEUED} waiting)`));
      }
      const pending: Pending = { request, resolve, reject, signal };
      if (signal) {
        pending.onAbort = () => this.#abort(pending);
        signal.addEventListener("abort", pending.onAbort, { once: true });
      }
      this.#queue.push(pending);
      this.#pump();
    });
  }

  close(): Promise<void> {
    this.#closing ??= (async () => {
      this.#fail(new TransportError("runtime transport is closed"));
      const child = this.#child;
      if (!child) return;
      child.ref(); // hold Bun open until the child is gone
      // EOF lets the runtime finish its current request and exit 0.
      void Promise.resolve(child.stdin.end()).catch(() => {});
      const timer = setTimeout(() => child.kill("SIGKILL"), CLOSE_GRACE_MS);
      await this.#finished;
      clearTimeout(timer);
    })();
    return this.#closing;
  }

  #start(): Bun.Subprocess<"pipe", "pipe", "pipe"> {
    const child = Bun.spawn(this.#argv, { stdin: "pipe", stdout: "pipe", stderr: "pipe" });
    this.#child = child;
    child.unref(); // see #pump
    this.#finished = Promise.allSettled([
      this.#read(child.stdout),
      this.#drain(child.stderr),
      child.exited,
    ]).then(() => {
      const how = child.signalCode
        ? `was killed by ${child.signalCode}`
        : `exited ${child.exitCode}`;
      const tail = this.#stderr.trim();
      this.#fail(new TransportError(`oxide-runtime ${how}${tail ? `: ${tail}` : ""}`));
    });
    return child;
  }

  #pump(): void {
    if (this.#active || this.#failure) return;
    const next = this.#queue.shift();
    // An idle runtime must not keep Bun alive: a caller that forgets close()
    // still exits, and the child then sees EOF and exits too.
    if (!next) {
      this.#child?.unref();
      return;
    }
    this.#active = next;
    let child = this.#child;
    try {
      child ??= this.#start();
    } catch (err) {
      const message = `cannot start ${this.#argv[0]}: ${(err as Error).message}`;
      this.#fail(new TransportError(message, { cause: err }));
      return;
    }
    child.ref();
    // A failed write means the child is gone or unusable; stop it and let
    // exit handling report why.
    const stop = () => child.kill("SIGKILL");
    try {
      child.stdin.write(`${JSON.stringify(next.request)}\n`);
      void Promise.resolve(child.stdin.flush()).catch(stop);
    } catch {
      stop();
    }
  }

  #abort(pending: Pending): void {
    const at = this.#queue.indexOf(pending);
    if (at >= 0) this.#queue.splice(at, 1);
    else if (this.#active === pending) pending.abandoned = true;
    else return;
    pending.reject(pending.signal?.reason);
  }

  #settle(pending: Pending): void {
    if (pending.onAbort) pending.signal?.removeEventListener("abort", pending.onAbort);
  }

  #onLine(line: string): void {
    // Closed or failed: late answers (e.g. to work abandoned by close) are dropped.
    if (this.#failure) return;
    const active = this.#active;
    if (!active) throw new ProtocolError("oxide-runtime answered with no request pending");
    const response = decodeResponse(JSON.parse(line));
    // With one request on the wire, a null-id contract error is its answer.
    checkCorrelation(active.request, response);
    this.#active = undefined;
    this.#settle(active);
    if (!active.abandoned) active.resolve(response);
    this.#pump();
  }

  async #read(stdout: ReadableStream<Uint8Array>): Promise<void> {
    const decoder = new TextDecoder("utf-8", { fatal: true });
    let buffer = "";
    try {
      for await (const chunk of stdout) {
        buffer += decoder.decode(chunk, { stream: true });
        for (let nl = buffer.indexOf("\n"); nl >= 0; nl = buffer.indexOf("\n")) {
          const line = buffer.slice(0, nl);
          buffer = buffer.slice(nl + 1);
          this.#onLine(line);
        }
        if (buffer.length > MAX_RESPONSE_CHARS) {
          throw new ProtocolError(`response line exceeds ${MAX_RESPONSE_CHARS} characters`);
        }
      }
    } catch (err) {
      // Framing can no longer be trusted: stop the process we own.
      const failure = err instanceof ProtocolError ? err : new ProtocolError(String(err));
      this.#fail(failure);
      this.#child?.kill("SIGKILL");
    }
  }

  async #drain(stderr: ReadableStream<Uint8Array>): Promise<void> {
    const decoder = new TextDecoder();
    for await (const chunk of stderr) {
      this.#stderr = (this.#stderr + decoder.decode(chunk, { stream: true })).slice(
        -STDERR_TAIL_CHARS,
      );
    }
  }

  /** Records the first failure and rejects everything pending with it. */
  #fail(failure: Error): void {
    if (this.#failure) return;
    this.#failure = failure;
    const pending = [...(this.#active ? [this.#active] : []), ...this.#queue];
    this.#active = undefined;
    this.#queue = [];
    for (const p of pending) {
      this.#settle(p);
      if (!p.abandoned) p.reject(failure);
    }
  }
}
