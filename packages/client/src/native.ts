// Serves requests in this process through the @oxide/native addon
// (packages/native), which keeps OXIDE's repository state and embedding model
// warm between calls. Each call answers what `oxide <command> --json` would
// print, so `Oxide` validates it exactly as it does the process backend's.
import { createRequire } from "node:module";
import type {
  Backend,
  IndexOptions,
  LiteralOptions,
  Outcome,
  QueryOptions,
  SearchOptions,
} from "./backend.js";
import { OxideClientError } from "./errors.js";

/** The addon's surface (`packages/native/src/lib.rs`). */
interface NativeRepository {
  status(): Promise<NativeOutcome>;
  index(options: IndexOptions): Promise<NativeOutcome>;
  search(query: string, options: SearchOptions): Promise<NativeOutcome>;
  searchLiteral(pattern: string, options: LiteralOptions): Promise<NativeOutcome>;
  query(task: string, options: QueryOptions): Promise<NativeOutcome>;
}

interface NativeOutcome {
  ok: boolean;
  json: string;
}

interface NativeAddon {
  Repository: new (path: string) => NativeRepository;
}

let addon: NativeAddon | undefined;

/** Loaded on first use, so the process backend never needs the addon. */
function loadAddon(): NativeAddon {
  if (addon === undefined) {
    try {
      addon = createRequire(import.meta.url)("@oxide/native") as NativeAddon;
    } catch (cause) {
      throw new OxideClientError(
        "native",
        `the native backend is unavailable (build it with \`mise run native:build\`): ${
          cause instanceof Error ? cause.message : String(cause)
        }`,
        { cause },
      );
    }
  }
  return addon;
}

export class NativeBackend implements Backend {
  readonly #repository: NativeRepository;

  constructor(cwd: string) {
    this.#repository = new (loadAddon().Repository)(cwd);
  }

  status(): Promise<Outcome> {
    return settle(() => this.#repository.status());
  }

  index(options: IndexOptions): Promise<Outcome> {
    return settle(() => this.#repository.index(options));
  }

  search(query: string, options: SearchOptions): Promise<Outcome> {
    return settle(() => this.#repository.search(query, options));
  }

  searchLiteral(pattern: string, options: LiteralOptions): Promise<Outcome> {
    return settle(() => this.#repository.searchLiteral(pattern, options));
  }

  query(task: string, options: QueryOptions): Promise<Outcome> {
    return settle(() => this.#repository.query(task, options));
  }
}

/** A rejected call (an argument the addon rejects, a panic) has no JSON answer. */
async function settle(call: () => Promise<NativeOutcome>): Promise<Outcome> {
  let outcome: NativeOutcome;
  try {
    outcome = await call();
  } catch (cause) {
    throw new OxideClientError(
      "native",
      `the native call failed without an answer: ${
        cause instanceof Error ? cause.message : String(cause)
      }`,
      { cause },
    );
  }
  return { ok: outcome.ok, json: JSON.parse(outcome.json) };
}
