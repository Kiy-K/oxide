// Runs one `oxide <command> --json` process per request. The binary prints its
// answer on stdout: the result with exit 0, or the error envelope with exit 1
// (`src/main.rs`). Anything else (a usage error, a signal) has no JSON answer.
import { spawn } from "node:child_process";
import type { Backend, IndexOptions, Outcome, QueryOptions, SearchOptions } from "./backend.ts";
import { OxideClientError } from "./errors.ts";

export class ProcessBackend implements Backend {
  readonly #binary: string;
  readonly #cwd: string;
  readonly #env: NodeJS.ProcessEnv;

  constructor(binary: string, cwd: string, env: NodeJS.ProcessEnv) {
    this.#binary = binary;
    this.#cwd = cwd;
    this.#env = env;
  }

  status(): Promise<Outcome> {
    return this.run("status", ["."]);
  }

  index(options: IndexOptions): Promise<Outcome> {
    return this.run("index", [".", ...flag("--rebuild", options.rebuild)]);
  }

  search(query: string, options: SearchOptions): Promise<Outcome> {
    return this.run("search", [
      "--path",
      ".",
      ...value("--limit", options.limit),
      ...value("--mode", options.mode),
      ...value("--profile", options.profile),
      ...flag("--no-expand", options.expand === false),
      ...flag("--blast-radius", options.blastRadius),
      "--",
      query,
    ]);
  }

  query(task: string, options: QueryOptions): Promise<Outcome> {
    return this.run("query", [
      "--path",
      ".",
      ...value("--budget-tokens", options.budgetTokens),
      ...value("--profile", options.profile),
      ...flag("--blast-radius", options.blastRadius),
      "--",
      task,
    ]);
  }

  private run(command: string, args: string[]): Promise<Outcome> {
    return new Promise((resolve, reject) => {
      const child = spawn(this.#binary, [command, "--json", ...args], {
        cwd: this.#cwd,
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
