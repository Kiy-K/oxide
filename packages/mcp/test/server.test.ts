// The Node-run adapter over real stdio, without Rust: it must serve the Rust
// server's surface verbatim and reject malformed arguments exactly as
// `src/mcp.rs` does (JSON-RPC -32602, same message), before ever running
// `oxide`. Behavior against the real binary: test/integration/parity.test.ts.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { connect, type Session } from "./stdio.ts";

const main = fileURLToPath(new URL("../src/main.ts", import.meta.url));
const surface = JSON.parse(
  readFileSync(new URL("../../../fixtures/mcp/surface.json", import.meta.url), "utf8"),
);

let session: Session;
before(async () => {
  session = await connect(process.execPath, [main], {
    cwd: tmpdir(),
    env: { ...process.env, OXIDE_BIN: "/nonexistent/oxide" },
  });
});
after(() => session.close());

test("initialize carries the Rust server's name and instructions", () => {
  const info = session.initialized.serverInfo as { name: string };
  assert.equal(info.name, surface.serverName);
  assert.equal(session.initialized.instructions, surface.instructions);
  assert.deepEqual(session.initialized.capabilities, { tools: {} });
});

test("tools/list is the Rust surface, unchanged", async () => {
  const reply = await session.request("tools/list");
  assert.deepEqual(reply.result?.tools, surface.tools);
});

const malformed: [string, Record<string, unknown>, string][] = [
  ["query", {}, "task must be a string"],
  ["query", { task: "  " }, "task must not be empty"],
  ["query", { task: "t", extra: 1 }, "unknown argument: extra"],
  ["query", { task: "t", path: "" }, "path must not be empty"],
  ["query", { task: "t", budget_tokens: -1 }, "budget_tokens must be a non-negative integer"],
  ["query", { task: "t", budget_tokens: 1.5 }, "budget_tokens must be a non-negative integer"],
  ["query", { task: "t", profile: "turbo" }, "profile must be fast|balanced|quality, got turbo"],
  ["query", { task: "t", profile: 3 }, "profile must be a string"],
  ["query", { task: "t", git: "yes" }, "git must be a boolean"],
  ["search", { query: "q", mode: "lexical" }, 'mode must be "literal" if present, got "lexical"'],
  ["search", { mode: "literal" }, "query must be a string"],
  ["search", { query: "q", limit: "5" }, "limit must be a non-negative integer"],
  ["search", { query: "q", blast_radius: 1 }, "blast_radius must be a boolean"],
  ["search", { query: "q", bogus: true }, "unknown argument: bogus"],
  ["nope", {}, "tool not found"],
];

for (const [tool, args, message] of malformed) {
  test(`${tool} ${JSON.stringify(args)} is -32602: ${message}`, async () => {
    const reply = await session.call(tool, args);
    assert.deepEqual(reply.error, { code: -32602, message });
  });
}

test("a binary that cannot run is an internal error, not a tool result", async () => {
  const reply = await session.call("search", { query: "retry" });
  assert.equal(reply.error?.code, -32603);
});
