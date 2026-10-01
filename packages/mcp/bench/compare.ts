// Rust `oxide mcp` vs the Node-run and Deno-compiled TS adapter (#36 T3).
// Not a test: run by hand after `mise run ts:integration` built dist/oxide-mcp,
//   OXIDE_BIN=$PWD/target/release/oxide node packages/mcp/bench/compare.ts [hashed|native]
// and record the output in docs/ts-mcp-adapter/README.md.
//
// Per server: startup (spawn -> initialize answered, fresh process each
// time), warm per-call latency of search and query in one session (median /
// p95 of N after warm-up), and the server process's peak RSS (VmHWM) after
// those calls. The TS servers' per-call `oxide` children are not in their RSS.
import { execFileSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { connect } from "../test/stdio.ts";

const binary = process.env.OXIDE_BIN;
if (!binary) throw new Error("set OXIDE_BIN to an oxide binary");
const embed = process.argv[2] ?? "hashed";
const N = 30;
const WARMUP = 3;
const STARTS = 10;

const tmp = mkdtempSync(join(tmpdir(), "oxide-mcp-bench-"));
const repo = join(tmp, "repo");
cpSync(fileURLToPath(new URL("../../../src", import.meta.url)), repo, { recursive: true });
const env: NodeJS.ProcessEnv = {
  ...process.env,
  OXIDE_BIN: binary,
  PATH: `${dirname(binary)}${delimiter}${process.env.PATH ?? ""}`,
};
for (const key of ["OXIDE_EMBED_URL", "OXIDE_EMBED_MODEL", "OXIDE_RETRIEVAL_MODE"]) {
  delete env[key];
}
// `native` keeps HOME so the cached Hugging Face weights are found.
if (embed === "native") delete env.OXIDE_EMBED_NATIVE;
else Object.assign(env, { OXIDE_EMBED_NATIVE: "hashed", HOME: tmp, XDG_CONFIG_HOME: tmp });
execFileSync(binary, ["index", repo, "--json"], { env });

const main = fileURLToPath(new URL("../src/main.ts", import.meta.url));
const compiled = fileURLToPath(new URL("../dist/oxide-mcp", import.meta.url));
const servers: [string, string, string[]][] = [
  ["Rust `oxide mcp`", binary, ["mcp"]],
  ["TS adapter, Node", process.execPath, [main]],
  ["TS adapter, Deno binary", compiled, []],
];

const stats = (samples: number[]) => {
  samples.sort((a, b) => a - b);
  const at = (q: number) => samples[Math.min(samples.length - 1, Math.floor(q * samples.length))];
  return `${at(0.5)?.toFixed(1)} / ${at(0.95)?.toFixed(1)}`;
};

async function timed(fn: () => Promise<unknown>, n: number, warmup: number) {
  const samples: number[] = [];
  for (let i = 0; i < warmup + n; i++) {
    const start = performance.now();
    await fn();
    if (i >= warmup) samples.push(performance.now() - start);
  }
  return stats(samples);
}

const peakRss = (pid: number) => {
  const line = readFileSync(`/proc/${pid}/status`, "utf8")
    .split("\n")
    .find((l) => l.startsWith("VmHWM:"));
  return `${(Number(line?.split(/\s+/)[1]) / 1024).toFixed(1)} MB`;
};

console.log(`embedder: ${embed}; repo: oxide src/ copy; N=${N} after ${WARMUP} warm-up`);
console.log("| server | startup ms | search ms | query ms | peak RSS |\n|---|---|---|---|---|");
for (const [label, command, args] of servers) {
  const startup = await timed(
    async () => (await connect(command, args, { cwd: repo, env })).close(),
    STARTS,
    1,
  );
  const session = await connect(command, args, { cwd: repo, env });
  const call = async (name: string, a: Record<string, unknown>) => {
    const reply = await session.call(name, a);
    if (reply.error !== undefined || reply.result?.isError) {
      throw new Error(`${label} ${name} failed: ${JSON.stringify(reply)}`);
    }
  };
  const search = await timed(() => call("search", { query: "retry with backoff" }), N, WARMUP);
  const query = await timed(() => call("query", { task: "how is the index refreshed" }), N, WARMUP);
  const rss = peakRss(session.child.pid as number);
  await session.close();
  console.log(`| ${label} | ${startup} | ${search} | ${query} | ${rss} |`);
}

const mb = (path: string) => `${(statSync(path).size / 1024 / 1024).toFixed(1)} MB`;
console.log(
  `\nsizes: oxide ${mb(binary)}, dist/oxide-mcp ${mb(compiled)}, node ${mb(process.execPath)}`,
);
