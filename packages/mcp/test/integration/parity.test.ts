// Conformance against the canonical server: the same requests go to the Rust
// `oxide mcp`, the Node-run adapter and the Deno-compiled binary
// (dist/oxide-mcp), all on one indexed copy of fixtures/py_repo with the
// hashed embedder. Results must match: protocol errors by code and message,
// tool results by isError, structuredContent and the parsed JSON payload
// (key order and whitespace are not compared). Run by `mise run ts:integration`.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cpSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, dirname, join } from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { connect, type Reply, type Session } from "../stdio.ts";

const binary = process.env.OXIDE_BIN;
if (!binary) {
  throw new Error("OXIDE_BIN must point at an oxide binary (mise run ts:integration sets it)");
}
const main = fileURLToPath(new URL("../../src/main.ts", import.meta.url));
const compiled = fileURLToPath(new URL("../../dist/oxide-mcp", import.meta.url));
const pyRepo = fileURLToPath(new URL("../../../../fixtures/py_repo", import.meta.url));

const tmp = mkdtempSync(join(tmpdir(), "oxide-mcp-parity-"));
const repo = join(tmp, "repo");
const unindexed = join(tmp, "unindexed");
const env: NodeJS.ProcessEnv = {
  ...process.env,
  HOME: tmp,
  XDG_CONFIG_HOME: tmp,
  OXIDE_EMBED_NATIVE: "hashed",
  OXIDE_BIN: binary,
  // The compiled binary may only run the `oxide` found on PATH.
  PATH: `${dirname(binary)}${delimiter}${process.env.PATH ?? ""}`,
};
for (const key of ["OXIDE_EMBED_URL", "OXIDE_EMBED_MODEL", "OXIDE_RETRIEVAL_MODE"]) {
  delete env[key];
}

const servers: Record<string, Session> = {};
before(async () => {
  cpSync(pyRepo, repo, { recursive: true });
  cpSync(pyRepo, unindexed, { recursive: true });
  execFileSync(binary, ["index", repo, "--json"], { env });
  const options = { cwd: repo, env };
  servers.rust = await connect(binary, ["mcp"], options);
  servers.node = await connect(process.execPath, [main], options);
  servers.deno = await connect(compiled, [], options);
});
after(() => Promise.all(Object.values(servers).map((server) => server.close())));

function comparable(reply: Reply) {
  if (reply.error !== undefined) return { error: reply.error };
  const result = reply.result ?? {};
  const content = result.content as { type: string; text: string }[];
  assert.equal(content.length, 1);
  assert.equal(content[0]?.type, "text");
  return {
    isError: result.isError,
    structuredContent: result.structuredContent,
    payload: JSON.parse(content[0]?.text ?? ""),
  };
}

async function parity(send: (server: Session) => Promise<Reply>) {
  const rust = comparable(await send(servers.rust as Session));
  for (const name of ["node", "deno"]) {
    assert.deepEqual(comparable(await send(servers[name] as Session)), rust, `${name} vs rust`);
  }
  return rust;
}

test("initialize matches apart from the server version", () => {
  const pick = (s: Session) => ({
    protocolVersion: s.initialized.protocolVersion,
    capabilities: s.initialized.capabilities,
    instructions: s.initialized.instructions,
    name: (s.initialized.serverInfo as { name: string }).name,
  });
  const rust = pick(servers.rust as Session);
  assert.deepEqual(pick(servers.node as Session), rust);
  assert.deepEqual(pick(servers.deno as Session), rust);
});

test("tools/list is identical", async () => {
  const rust = await (servers.rust as Session).request("tools/list");
  for (const name of ["node", "deno"]) {
    assert.deepEqual(await (servers[name] as Session).request("tools/list"), rust, name);
  }
});

const calls: [string, Record<string, unknown>][] = [
  ["query", { task: "where is retry logic" }],
  [
    "query",
    { task: "where is retry logic", budget_tokens: 600, blast_radius: true, profile: " Quality " },
  ],
  ["query", { task: "fix the cache expiry", git: true }],
  ["search", { query: "retry with exponential backoff" }],
  [
    "search",
    { query: "retry with exponential backoff", limit: 3, blast_radius: true, profile: "fast" },
  ],
  ["search", { query: "RetryPolicy", mode: "literal", limit: 2 }],
  ["search", { query: "zzqqxx", mode: "literal" }],
  [
    "search",
    { query: "RetryPolicy", mode: "literal", path: unindexed, profile: "ignored-in-literal" },
  ],
  ["search", { query: "retry", path: unindexed }],
  ["search", { query: "retry", path: join(tmp, "no-such-repo") }],
  ["search", { query: "retry", path: "-x" }],
  ["query", { task: "" }],
  ["query", { task: "t", profile: "turbo" }],
  ["search", { query: "q", mode: "hybrid" }],
  ["search", { query: "q", limit: -1 }],
  ["search", { query: "q", unknown: 1 }],
  ["missing_tool", {}],
];

for (const [tool, args] of calls) {
  test(`${tool} ${JSON.stringify(args)}`, async () => {
    await parity((server) => server.call(tool, args));
  });
}

test("the cases above cover success, OXIDE errors and protocol errors", async () => {
  const ok = await parity((s) => s.call("search", { query: "retry" }));
  assert.equal(ok.isError, false);
  const missing = await parity((s) => s.call("search", { query: "retry", path: unindexed }));
  assert.equal(missing.isError, true);
  assert.deepEqual(Object.keys(missing.structuredContent as object), ["error"]);
  const malformed = await parity((s) => s.call("query", {}));
  assert.equal(malformed.error?.code, -32602);
});
