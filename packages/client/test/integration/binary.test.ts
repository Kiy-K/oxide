// The client against the real `oxide` binary ($OXIDE_BIN), on a fresh copy of
// fixtures/py_repo with the offline hashed embedder and no user config.
// Run by `mise run ts:integration`; never part of the cached unit tests.
import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { Oxide, OxideError } from "../../src/index.ts";

const binary = process.env.OXIDE_BIN;
if (!binary) {
  throw new Error("OXIDE_BIN must point at an oxide binary (mise run ts:integration sets it)");
}

const tmp = mkdtempSync(join(tmpdir(), "oxide-client-it-"));
const env = {
  HOME: tmp,
  XDG_CONFIG_HOME: tmp,
  OXIDE_EMBED_NATIVE: "hashed",
  OXIDE_EMBED_URL: undefined,
  OXIDE_EMBED_MODEL: undefined,
  OXIDE_RETRIEVAL_MODE: undefined,
  OXIDE_TELEMETRY: undefined,
};

// One test: the steps share one repository and must run in this order.
test("status, index, search and query against the real binary", async () => {
  const repo = join(tmp, "repo");
  cpSync(fileURLToPath(new URL("../../../../fixtures/py_repo", import.meta.url)), repo, {
    recursive: true,
  });
  const oxide = new Oxide({ cwd: repo, binary, env });

  const empty = await oxide.status();
  assert.equal(empty.index_exists, false);
  assert.equal(empty.embedder, null);

  await assert.rejects(oxide.search("retry"), (error: unknown) => {
    assert.ok(error instanceof OxideError);
    assert.equal(error.code, "index_missing");
    assert.equal(error.action, "index");
    return true;
  });

  const fresh = await oxide.index();
  assert.ok(fresh.new_symbols > 0);
  assert.equal(fresh.embed_failures, 0);
  const unchanged = await oxide.index();
  assert.equal(unchanged.changed_files, 0);

  const status = await oxide.status();
  assert.equal(status.is_current, true);
  assert.equal(status.symbols, fresh.new_symbols);

  const hits = await oxide.search("retry with exponential backoff", { limit: 3 });
  assert.equal(hits.length, 3);

  const blast = await oxide.search("retry with exponential backoff", {
    limit: 2,
    blastRadius: true,
    profile: "quality",
  });
  assert.ok(blast.some((hit) => (hit.blast_radius?.length ?? 0) > 0));

  assert.deepEqual(await oxide.search("zzqqxx", { mode: "lexical" }), []);

  const pack = await oxide.query("where is retry logic", { budgetTokens: 600 });
  assert.ok(pack.items.length > 0);
  assert.ok(pack.used_tokens <= 600);

  const rebuilt = await oxide.index({ rebuild: true });
  assert.equal(rebuilt.changed_files, fresh.scanned_files);
});

test("indexing a directory with no source files is a structured error", async () => {
  const dir = join(tmp, "empty");
  mkdirSync(dir);
  const oxide = new Oxide({ cwd: dir, binary, env });
  await assert.rejects(oxide.index(), (error: unknown) => {
    assert.ok(error instanceof OxideError);
    assert.equal(error.code, "no_source_files");
    assert.equal(error.action, "stop");
    return true;
  });
});
