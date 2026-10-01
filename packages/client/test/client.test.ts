// Client behaviour without Rust: a fake `oxide` replays the committed
// protocol fixtures (real binary output) and records the argv it was given.
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { Oxide, OxideClientError, OxideError } from "../src/index.ts";

const fake = fileURLToPath(new URL("./fake-oxide.mjs", import.meta.url));
const fixtures = fileURLToPath(new URL("../../../fixtures/protocol/", import.meta.url));

interface Reply {
  fixture?: string;
  stdout?: string;
  exit?: number;
  stderr?: string;
  signal?: string;
}

function client(reply: Reply) {
  const dir = mkdtempSync(join(tmpdir(), "oxide-client-"));
  const argvFile = join(dir, "argv.json");
  let stdoutFile: string | undefined;
  if (reply.fixture !== undefined) {
    stdoutFile = join(fixtures, `${reply.fixture}.json`);
  } else if (reply.stdout !== undefined) {
    stdoutFile = join(dir, "stdout");
    writeFileSync(stdoutFile, reply.stdout);
  }
  const oxide = new Oxide({
    cwd: dir,
    binary: fake,
    env: {
      FAKE_ARGV_FILE: argvFile,
      FAKE_STDOUT_FILE: stdoutFile,
      FAKE_EXIT: String(reply.exit ?? 0),
      FAKE_STDERR: reply.stderr,
      FAKE_SIGNAL: reply.signal,
    },
  });
  const argv = (): string[] => JSON.parse(readFileSync(argvFile, "utf8"));
  return { oxide, argv };
}

test("status returns the validated result", async () => {
  const { oxide, argv } = client({ fixture: "status-current" });
  const status = await oxide.status();
  assert.equal(status.is_current, true);
  assert.deepEqual(argv(), ["status", "--json", "."]);
});

test("index maps options to flags", async () => {
  const { oxide, argv } = client({ fixture: "index-fresh" });
  const result = await oxide.index({ rebuild: true });
  assert.ok(result.new_symbols > 0);
  assert.deepEqual(argv(), ["index", "--json", ".", "--rebuild"]);
});

test("search maps every option and keeps the query a positional", async () => {
  const { oxide, argv } = client({ fixture: "search-blast-radius" });
  const hits = await oxide.search("--looks-like-a-flag", {
    limit: 2,
    mode: "lexical",
    profile: "fast",
    expand: false,
    blastRadius: true,
  });
  assert.ok(hits.length > 0);
  assert.deepEqual(argv(), [
    "search",
    "--json",
    "--path",
    ".",
    "--limit",
    "2",
    "--mode",
    "lexical",
    "--profile",
    "fast",
    "--no-expand",
    "--blast-radius",
    "--",
    "--looks-like-a-flag",
  ]);
});

test("search with defaults passes no optional flags", async () => {
  const { oxide, argv } = client({ fixture: "search-empty" });
  assert.deepEqual(await oxide.search("x"), []);
  assert.deepEqual(argv(), ["search", "--json", "--path", ".", "--", "x"]);
});

test("query maps options to `oxide query`", async () => {
  const { oxide, argv } = client({ fixture: "context" });
  const pack = await oxide.query("where is retry logic", { budgetTokens: 600 });
  assert.ok(pack.items.length > 0);
  assert.deepEqual(argv(), [
    "query",
    "--json",
    "--path",
    ".",
    "--budget-tokens",
    "600",
    "--",
    "where is retry logic",
  ]);
});

test("an OXIDE error envelope becomes OxideError with its code and action", async () => {
  const { oxide } = client({ fixture: "error-index-missing", exit: 1 });
  await assert.rejects(oxide.search("retry"), (error: unknown) => {
    assert.ok(error instanceof OxideError);
    assert.equal(error.code, "index_missing");
    assert.equal(error.action, "index");
    return true;
  });
});

test("an unknown error code is preserved, not rejected", async () => {
  const stdout = JSON.stringify({
    error: { code: "future_failure", action: "retry", message: "later" },
  });
  const { oxide } = client({ stdout, exit: 1 });
  await assert.rejects(oxide.status(), { name: "OxideError", code: "future_failure" });
});

test("a usage error without JSON is an exit failure that keeps stderr", async () => {
  const stderr = "error: unexpected argument '--nope' found\n";
  const { oxide } = client({ exit: 2, stderr });
  await assert.rejects(oxide.status(), (error: unknown) => {
    assert.ok(error instanceof OxideClientError);
    assert.equal(error.reason, "exit");
    assert.equal(error.exitCode, 2);
    assert.equal(error.stderr, stderr);
    return true;
  });
});

test("a process killed by a signal is an exit failure", async () => {
  const { oxide } = client({ signal: "SIGTERM" });
  await assert.rejects(oxide.status(), (error: unknown) => {
    assert.ok(error instanceof OxideClientError);
    assert.equal(error.reason, "exit");
    assert.equal(error.signal, "SIGTERM");
    return true;
  });
});

test("exit 1 with non-JSON stdout is an exit failure", async () => {
  const { oxide } = client({ stdout: "panicked at src/main.rs", exit: 1 });
  await assert.rejects(oxide.status(), { name: "OxideClientError", reason: "exit" });
});

test("non-JSON success output is invalid output", async () => {
  const { oxide } = client({ stdout: "files: 9\n" });
  await assert.rejects(oxide.status(), { name: "OxideClientError", reason: "invalid-output" });
});

test("JSON that breaks the protocol is invalid output", async () => {
  const { oxide } = client({ fixture: "search-hybrid" });
  await assert.rejects(oxide.status(), { name: "OxideClientError", reason: "invalid-output" });
});

test("a malformed error envelope is invalid output", async () => {
  const { oxide } = client({ stdout: '{"error":{"code":"x"}}', exit: 1 });
  await assert.rejects(oxide.status(), { name: "OxideClientError", reason: "invalid-output" });
});

test("a missing binary is a spawn failure", async () => {
  const oxide = new Oxide({ cwd: tmpdir(), binary: join(tmpdir(), "no-such-oxide-binary") });
  await assert.rejects(oxide.status(), { name: "OxideClientError", reason: "spawn" });
});
