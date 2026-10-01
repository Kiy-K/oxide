// Runs one `oxide <command> --json` process per request. The binary prints its
// answer on stdout: the result with exit 0, or the error envelope with exit 1
// (`src/main.rs`). Anything else (a usage error, a signal) has no JSON answer.
import { spawn } from "node:child_process";
import type {
  Backend,
  IndexOptions,
  LiteralOptions,
  Outcome,
  QueryOptions,
  SearchOptions,
} from "./backend.js";
import { OxideClientError } from "./errors.js";

export class ProcessBackend implements Backend {
  readonly #binary: string;
  readonly #env: NodeJS.ProcessEnv | undefined;
  /** Where oxide runs: inherited when the repository is passed explicitly. */
  readonly #spawnCwd: string | undefined;
  /**
   * The repository as an explicit argument, or nothing so oxide discovers it
   * from `cwd`. Written so a path starting with `-` is never read as a flag:
   * `--path=<repo>`, and the positional form goes after `--`.
   */
  readonly #positional: string[];
  readonly #pathFlag: string[];

  constructor(binary: string, cwd: string, env: NodeJS.ProcessEnv | undefined, discover: boolean) {
    this.#binary = binary;
    this.#env = env;
    // Explicit: a missing repository is oxide's own `repository_not_found`,
    // not a failure to spawn inside a directory that does not exist.
    this.#spawnCwd = discover ? cwd : undefined;
    this.#positional = discover ? [] : ["--", cwd];
    this.#pathFlag = discover ? [] : [`--path=${cwd}`];
  }

  status(): Promise<Outcome> {
    return this.run("status", this.#positional);
  }

  index(options: IndexOptions): Promise<Outcome> {
    return this.run("index", [...flag("--rebuild", options.rebuild), ...this.#positional]);
  }

  search(query: string, options: SearchOptions): Promise<Outcome> {
    return this.run("search", [
      ...this.#pathFlag,
      ...value("--limit", options.limit),
      ...value("--mode", options.mode),
      ...value("--profile", options.profile),
      ...flag("--no-expand", options.expand === false),
      ...flag("--blast-radius", options.blastRadius),
      "--",
      query,
    ]);
  }

  searchLiteral(pattern: string, options: LiteralOptions): Promise<Outcome> {
    return this.run("search", [
      ...this.#pathFlag,
      "--mode",
      "literal",
      ...value("--limit", options.limit),
      "--",
      pattern,
    ]);
  }

  query(task: string, options: QueryOptions): Promise<Outcome> {
    return this.run("query", [
      ...this.#pathFlag,
      ...value("--budget-tokens", options.budgetTokens),
      ...value("--profile", options.profile),
      ...flag("--blast-radius", options.blastRadius),
      ...flag("--git", options.git),
      "--",
      task,
    ]);
  }

  private run(command: string, args: string[]): Promise<Outcome> {
    return new Promise((resolve, reject) => {
      const child = spawn(this.#binary, [command, "--json", ...args], {
        cwd: this.#spawnCwd,
        env: this.#env,
        stdio: ["ignore", "pipe", "pipe"],
      });
      const stdout: Buffer[] = [];
      const stderr: Buffer[] = [];
      child.stdout.on("data", (chunk: Buffer) => stdout.push(chunk));
      child.stderr.on("data", (chunk: Buffer) => stderr.push(chunk));
      child.on("error", (cause) => {
        reject(
          new OxideClientError("spawn", `could not run ${this.#binary}: ${cause.message}`, {
            cause,
          }),
        );
      });
      child.on("close", (exitCode, signal) => {
        const details = { exitCode, signal, stderr: Buffer.concat(stderr).toString("utf8") };
        if (exitCode === 0 || exitCode === 1) {
          const json = parseJson(Buffer.concat(stdout).toString("utf8"));
          if (json !== undefined) {
            resolve({ ok: exitCode === 0, json });
            return;
          }
          if (exitCode === 0) {
            reject(
              new OxideClientError("invalid-output", "oxide printed non-JSON output", details),
            );
            return;
          }
        }
        const how = signal === null ? `exit status ${exitCode}` : `signal ${signal}`;
        reject(new OxideClientError("exit", `oxide ended with ${how} and no JSON answer`, details));
      });
    });
  }
}

function value(name: string, option: string | number | undefined): string[] {
  return option === undefined ? [] : [name, String(option)];
}

function flag(name: string, enabled: boolean | undefined): string[] {
  return enabled ? [name] : [];
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}
