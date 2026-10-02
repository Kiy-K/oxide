import type { ErrorAction } from "@oxide/protocol";

/**
 * A failure OXIDE itself reported through its JSON error envelope. `code` is
 * the stable identity to branch on (open-ended: new codes may appear), and
 * `action` says what to do about it. `message` is prose for humans.
 */
export class OxideError extends Error {
  override readonly name = "OxideError";
  readonly code: string;
  readonly action: ErrorAction;

  constructor(code: string, action: ErrorAction, message: string) {
    super(message);
    this.code = code;
    this.action = action;
  }
}

/**
 * The client could not get a valid OXIDE answer: the binary failed to start
 * (`spawn`), ended without a JSON answer (`exit`, e.g. a usage error or a
 * signal), answered with something `@oxide/protocol` rejects
 * (`invalid-output`), or the native backend's addon could not load or failed
 * a call without an answer (`native`). `stderr` is kept for diagnostics only.
 */
export class OxideClientError extends Error {
  override readonly name = "OxideClientError";
  readonly reason: "spawn" | "exit" | "invalid-output" | "native";
  readonly exitCode: number | null;
  readonly signal: NodeJS.Signals | null;
  readonly stderr: string;

  constructor(
    reason: OxideClientError["reason"],
    message: string,
    details: {
      exitCode?: number | null;
      signal?: NodeJS.Signals | null;
      stderr?: string;
      cause?: unknown;
    } = {},
  ) {
    super(message, { cause: details.cause });
    this.reason = reason;
    this.exitCode = details.exitCode ?? null;
    this.signal = details.signal ?? null;
    this.stderr = details.stderr ?? "";
  }
}
