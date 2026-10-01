/**
 * Typed client for the `oxide` binary. Every answer is validated by
 * `@oxide/protocol`; failures become `OxideError` (OXIDE's own structured
 * error) or `OxideClientError` (no valid answer). CLI prose is never parsed.
 */
import {
  ContextResult,
  ErrorEnvelope,
  IndexResult,
  LiteralSearchResult,
  SearchResult,
  StatusResult,
} from "@oxide/protocol";
import type {
  Backend,
  IndexOptions,
  LiteralOptions,
  Outcome,
  QueryOptions,
  SearchOptions,
} from "./backend.js";
import { OxideClientError, OxideError } from "./errors.js";
import { ProcessBackend } from "./process.js";

export type {
  IndexOptions,
  LiteralOptions,
  Profile,
  QueryOptions,
  SearchOptions,
} from "./backend.js";
export { OxideClientError, OxideError } from "./errors.js";

export interface OxideOptions {
  /** The repository to operate on; every command targets it explicitly. */
  cwd: string;
  /**
   * Let oxide discover the repository by walking up from `cwd` for
   * `.git`/`.oxide`, as when it runs inside one, instead of targeting `cwd`.
   */
  discover?: boolean;
  /** The `oxide` executable; defaults to `oxide` on `PATH`. */
  binary?: string;
  /** Overrides merged over `process.env`; `undefined` removes a variable. */
  env?: Record<string, string | undefined>;
}

export class Oxide {
  readonly #backend: Backend;

  constructor(options: OxideOptions) {
    this.#backend = new ProcessBackend(
      options.binary ?? "oxide",
      options.cwd,
      options.env === undefined ? undefined : mergeEnv(process.env, options.env),
      options.discover ?? false,
    );
  }

  /** `oxide status`: index existence and freshness. Never builds an index. */
  async status(): Promise<StatusResult> {
    return answer(StatusResult, await this.#backend.status());
  }

  /** `oxide index`: build or incrementally refresh the index. */
  async index(options: IndexOptions = {}): Promise<IndexResult> {
    return answer(IndexResult, await this.#backend.index(options));
  }

  /** `oxide search`: ranked symbol evidence for a name, identifier or phrase. */
  async search(query: string, options: SearchOptions = {}): Promise<SearchResult> {
    return answer(SearchResult, await this.#backend.search(query, options));
  }

  /** `oxide search --mode literal`: exact substring matches over repository text; no index needed. */
  async searchLiteral(pattern: string, options: LiteralOptions = {}): Promise<LiteralSearchResult> {
    return answer(LiteralSearchResult, await this.#backend.searchLiteral(pattern, options));
  }

  /** `oxide query`: a token-budgeted context pack for a task or question. */
  async query(task: string, options: QueryOptions = {}): Promise<ContextResult> {
    return answer(ContextResult, await this.#backend.query(task, options));
  }
}

interface Schema<T> {
  safeParse(
    input: unknown,
  ): { success: true; data: T } | { success: false; error: { message: string } };
}

function answer<T>(schema: Schema<T>, outcome: Outcome): T {
  if (!outcome.ok) {
    const envelope = ErrorEnvelope.safeParse(outcome.json);
    if (!envelope.success) {
      throw new OxideClientError(
        "invalid-output",
        `oxide failed without a valid error envelope: ${envelope.error.message}`,
      );
    }
    const { code, action, message } = envelope.data.error;
    throw new OxideError(code, action, message);
  }
  const parsed = schema.safeParse(outcome.json);
  if (!parsed.success) {
    throw new OxideClientError(
      "invalid-output",
      `oxide output does not match @oxide/protocol: ${parsed.error.message}`,
    );
  }
  return parsed.data;
}

/** Only built when there are overrides; otherwise `oxide` simply inherits the environment. */
function mergeEnv(
  base: NodeJS.ProcessEnv,
  overrides: Record<string, string | undefined> = {},
): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...base };
  for (const [key, value] of Object.entries(overrides)) {
    if (value === undefined) {
      delete env[key];
    } else {
      env[key] = value;
    }
  }
  return env;
}
