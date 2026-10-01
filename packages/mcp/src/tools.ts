// The `query` and `search` tools, mirroring `src/mcp.rs` (the canonical
// server): the same argument checks in the same order, malformed arguments
// as JSON-RPC -32602, and OXIDE failures as `isError` results carrying the
// structured `{ error: { code, action, message } }`. Execution goes through
// @oxide/client; nothing here retrieves, ranks or reads the index.
import {
  type CallToolResult,
  INTERNAL_ERROR,
  INVALID_PARAMS,
  ProtocolError,
} from "@modelcontextprotocol/server";
import { Oxide, OxideClientError, OxideError, type Profile } from "@oxide/client";

/** `src/mcp.rs` DEFAULT_CONTEXT_BUDGET / DEFAULT_SEARCH_LIMIT. */
const DEFAULT_BUDGET_TOKENS = 4096;
const DEFAULT_SEARCH_LIMIT = 10;

export interface Runtime {
  /** The `oxide` executable. */
  binary: string;
  /** Where repository discovery starts when a call omits `path`. */
  cwd: string;
}

type Args = Record<string, unknown>;

export function callTool(name: string, args: Args, runtime: Runtime): Promise<CallToolResult> {
  switch (name) {
    case "query":
      return query(args, runtime);
    case "search":
      return search(args, runtime);
    default:
      throw invalid("tool not found");
  }
}

function query(args: Args, runtime: Runtime): Promise<CallToolResult> {
  rejectUnknown(args, ["task", "path", "budget_tokens", "profile", "blast_radius", "git"]);
  const task = requiredString(args, "task");
  const path = optionalString(args, "path");
  const budgetTokens = optionalCount(args, "budget_tokens") ?? DEFAULT_BUDGET_TOKENS;
  const profile = optionalProfile(args);
  const blastRadius = optionalBool(args, "blast_radius") ?? false;
  const git = optionalBool(args, "git") ?? false;
  return run(runtime, path, (oxide) =>
    oxide.query(task, { budgetTokens, profile, blastRadius, git }),
  );
}

function search(args: Args, runtime: Runtime): Promise<CallToolResult> {
  rejectUnknown(args, ["query", "path", "limit", "profile", "blast_radius", "mode"]);
  const mode = optionalString(args, "mode");
  if (mode !== undefined && mode !== "literal") {
    throw invalid(`mode must be "literal" if present, got ${JSON.stringify(mode)}`);
  }
  const text = requiredString(args, "query");
  const path = optionalString(args, "path");
  const limit = optionalCount(args, "limit") ?? DEFAULT_SEARCH_LIMIT;
  if (mode === "literal") {
    // As in Rust, `profile` and `blast_radius` are not read in literal mode.
    return run(runtime, path, (oxide) => oxide.searchLiteral(text, { limit }));
  }
  const profile = optionalProfile(args);
  const blastRadius = optionalBool(args, "blast_radius") ?? false;
  return run(runtime, path, (oxide) => oxide.search(text, { limit, profile, blastRadius }));
}

async function run(
  runtime: Runtime,
  path: string | undefined,
  call: (oxide: Oxide) => Promise<unknown>,
): Promise<CallToolResult> {
  const oxide =
    path === undefined
      ? new Oxide({ binary: runtime.binary, cwd: runtime.cwd, discover: true })
      : new Oxide({ binary: runtime.binary, cwd: path });
  try {
    const result = await call(oxide);
    return { content: [{ type: "text", text: JSON.stringify(result) }], isError: false };
  } catch (error) {
    if (error instanceof OxideError) {
      const payload = { error: { code: error.code, action: error.action, message: error.message } };
      return {
        content: [{ type: "text", text: JSON.stringify(payload) }],
        structuredContent: payload,
        isError: true,
      };
    }
    // Rust's counterpart is a failed blocking task: an internal JSON-RPC error.
    if (error instanceof OxideClientError) {
      throw new ProtocolError(INTERNAL_ERROR, error.message);
    }
    throw error;
  }
}

function invalid(message: string): ProtocolError {
  return new ProtocolError(INVALID_PARAMS, message);
}

function rejectUnknown(args: Args, allowed: string[]): void {
  const unknown = Object.keys(args).find((key) => !allowed.includes(key));
  if (unknown !== undefined) throw invalid(`unknown argument: ${unknown}`);
}

function requiredString(args: Args, key: string): string {
  const value = args[key];
  if (typeof value !== "string") throw invalid(`${key} must be a string`);
  if (value.trim() === "") throw invalid(`${key} must not be empty`);
  return value;
}

function optionalString(args: Args, key: string): string | undefined {
  return args[key] === undefined ? undefined : requiredString(args, key);
}

function optionalCount(args: Args, key: string): number | undefined {
  const value = args[key];
  if (value === undefined) return undefined;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw invalid(`${key} must be a non-negative integer`);
  }
  return value;
}

function optionalBool(args: Args, key: string): boolean | undefined {
  const value = args[key];
  if (value === undefined) return undefined;
  if (typeof value !== "boolean") throw invalid(`${key} must be a boolean`);
  return value;
}

/** `RetrievalMode::parse`: trimmed, case-insensitive. Absent defers to the binary. */
function optionalProfile(args: Args): Profile | undefined {
  const raw = optionalString(args, "profile");
  if (raw === undefined) return undefined;
  const profile = raw.trim().toLowerCase();
  if (profile === "fast" || profile === "balanced" || profile === "quality") return profile;
  throw invalid(`profile must be fast|balanced|quality, got ${raw}`);
}
