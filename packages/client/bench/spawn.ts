// Process-spawn overhead of @oxide/client (#36 T2). Not a test: run by hand,
//   OXIDE_BIN=$PWD/target/release/oxide node packages/client/bench/spawn.ts [hashed|native]
// and record the output in docs/ts-client-spawn-overhead/README.md.
//
// For each repository it times, per call (median and p95 of N runs after
// warm-up): the bare process floor (`oxide --version`), the client's status /
// search / query, and the same search and query over one long-lived
// `oxide mcp` process (what a persistent backend could save), and the
// client-side JSON.parse + schema validation of the same output.
import { execFileSync, spawn } from "node:child_process";
import { cpSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { ContextResult, SearchResult } from "@oxide/protocol";
import { Oxide } from "../src/index.ts";

const binary = process.env.OXIDE_BIN;
if (!binary) throw new Error("set OXIDE_BIN to an oxide binary");
const embed = process.argv[2] ?? "hashed";
const N = 30;
const WARMUP = 3;
const QUERY = "retry with exponential backoff";
const TASK = "where is retry logic";

const tmp = mkdtempSync(join(tmpdir(), "oxide-spawn-bench-"));
// `native` keeps HOME so the cached Hugging Face weights are found.
const env: Record<string, string | undefined> =
  embed === "native"
    ? { OXIDE_EMBED_NATIVE: undefined, OXIDE_EMBED_URL: undefined, OXIDE_EMBED_MODEL: undefined }
    : { OXIDE_EMBED_NATIVE: "hashed", HOME: tmp, XDG_CONFIG_HOME: tmp };
const childEnv: NodeJS.ProcessEnv = { ...process.env };
for (const [key, value] of Object.entries(env)) {
  if (value === undefined) delete childEnv[key];
  else childEnv[key] = value;
}

async function time(fn: () => Promise<unknown> | unknown): Promise<string> {
  const samples: number[] = [];
  for (let i = 0; i < WARMUP + N; i++) {
    const start = performance.now();
    await fn();
    if (i >= WARMUP) samples.push(performance.now() - start);
  }
  samples.sort((a, b) => a - b);
  const at = (q: number) => samples[Math.min(samples.length - 1, Math.floor(q * samples.length))];
  return `${at(0.5)?.toFixed(1)} / ${at(0.95)?.toFixed(1)}`;
}

function run(args: string[], cwd: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(binary as string, args, { cwd, env: childEnv, stdio: "ignore" });
    child.on("error", reject);
    child.on("close", () => resolve());
  });
}

// A minimal MCP stdio session: newline-delimited JSON-RPC.
async function mcpSession(cwd: string) {
  const child = spawn(binary as string, ["mcp"], { cwd, env: childEnv });
  const lines = createInterface({ input: child.stdout });
  const pending = new Map<number, (value: unknown) => void>();
  lines.on("line", (line) => {
    const message = JSON.parse(line) as { id?: number };
    if (message.id !== undefined) pending.get(message.id)?.(message);
  });
  let next = 1;
  const request = (method: string, params: unknown) =>
    new Promise((resolve) => {
      const id = next++;
      pending.set(id, resolve);
      child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
    });
  await request("initialize", {
    protocolVersion: "2025-06-18",
    capabilities: {},
    clientInfo: { name: "spawn-bench", version: "0" },
  });
  child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" })}\n`);
  return {
    // Fails the bench on a JSON-RPC or tool error, so an error path is never timed.
    call: async (name: string, args: unknown) => {
      const reply = (await request("tools/call", { name, arguments: args })) as {
        error?: unknown;
        result?: { isError?: boolean };
      };
      if (reply.error !== undefined || reply.result?.isError) {
        throw new Error(`MCP ${name} failed: ${JSON.stringify(reply)}`);
      }
    },
    close: () => child.kill(),
  };
}

async function bench(label: string, source: string) {
  const repo = join(tmp, label);
  cpSync(source, repo, { recursive: true });
  const oxide = new Oxide({ cwd: repo, binary: binary as string, env });
  const indexed = await oxide.index();
  const searchJson = execFileSync(binary as string, ["search", QUERY, "--json"], {
    cwd: repo,
    env: childEnv,
  }).toString();
  const queryJson = execFileSync(binary as string, ["query", TASK, "--json"], {
    cwd: repo,
    env: childEnv,
  }).toString();
  const mcp = await mcpSession(repo);
  const rows: [string, string][] = [
    ["process floor (`oxide --version`)", await time(() => run(["--version"], repo))],
    ["client.status()", await time(() => oxide.status())],
    ["client.search()", await time(() => oxide.search(QUERY))],
    ["client.query()", await time(() => oxide.query(TASK))],
    ["MCP search, warm process", await time(() => mcp.call("search", { query: QUERY }))],
    ["MCP query, warm process", await time(() => mcp.call("query", { task: TASK }))],
    [
      "parse + validate search output",
      await time(() => SearchResult.parse(JSON.parse(searchJson))),
    ],
    ["parse + validate query output", await time(() => ContextResult.parse(JSON.parse(queryJson)))],
  ];
  mcp.close();
  console.log(`\n${label}: ${indexed.scanned_files} files, ${indexed.new_symbols} symbols`);
  console.log(`(search output ${searchJson.length} B, query output ${queryJson.length} B)`);
  console.log("| call | median / p95 ms |\n|---|---|");
  for (const [name, value] of rows) console.log(`| ${name} | ${value} |`);
}

const root = fileURLToPath(new URL("../../../", import.meta.url));
console.log(`embedder: ${embed}, N=${N} after ${WARMUP} warm-up, ${process.version}`);
await bench("py_repo", join(root, "fixtures/py_repo"));
await bench("oxide-src", join(root, "src"));
