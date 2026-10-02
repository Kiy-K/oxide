// The client against the real OXIDE, on a fresh copy of fixtures/py_repo with
// the offline hashed embedder and no user config. One body, run once per
// backend (binary.test.ts, native.test.ts) to hold their parity. Run by
// `mise run ts:integration`; never part of the cached unit tests. Imports the
// client by package name (self-reference here), so the same files also run
// against an installed, packed `@oxide/client`.
import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { type Oxide, OxideError } from "@oxide/client";

export const tmp = mkdtempSync(join(tmpdir(), "oxide-client-it-"));

/** What each backend's environment must hold: `undefined` removes a variable. */
export const env: Record<string, string | undefined> = {
  HOME: tmp,
  XDG_CONFIG_HOME: tmp,
  OXIDE_EMBED_NATIVE: "hashed",
  OXIDE_EMBED_URL: undefined,
  OXIDE_EMBED_MODEL: undefined,
  OXIDE_RETRIEVAL_MODE: undefined,
  OXIDE_TELEMETRY: undefined,
};

export function suite(open: (cwd: string) => Oxide): void {
  // One test: the steps share one repository and must run in this order.
  test("status, index, search and query against the real OXIDE", async () => {
    const repo = join(tmp, "repo");
    cpSync(fileURLToPath(new URL("../../../../fixtures/py_repo", import.meta.url)), repo, {
      recursive: true,
    });
    const oxide = open(repo);

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

    const literal = await oxide.searchLiteral("def notify", { limit: 2 });
    assert.equal(literal.hits.length, 2);
    assert.equal(literal.truncated, true);

    await assert.rejects(oxide.search("retry", { profile: "bogus" as "fast" }), {
      name: "OxideError",
      code: "invalid_configuration",
    });

    const rebuilt = await oxide.index({ rebuild: true });
    assert.equal(rebuilt.changed_files, fresh.scanned_files);
  });

  test("indexing a directory with no source files is a structured error", async () => {
    const dir = join(tmp, "empty");
    mkdirSync(dir);
    const oxide = open(dir);
    await assert.rejects(oxide.index(), (error: unknown) => {
      assert.ok(error instanceof OxideError);
      assert.equal(error.code, "no_source_files");
      assert.equal(error.action, "stop");
      return true;
    });
  });

  test("a repository path that does not exist is OXIDE's own structured error", async () => {
    const oxide = open(join(tmp, "no-such-repo"));
    await assert.rejects(oxide.search("retry"), {
      name: "OxideError",
      code: "repository_not_found",
    });
  });

  test("a repository path that starts with a dash is a path, not a flag", async () => {
    const oxide = open("-not-a-flag");
    await assert.rejects(oxide.status(), { name: "OxideError", code: "repository_not_found" });
    await assert.rejects(oxide.index(), { name: "OxideError", code: "repository_not_found" });
    await assert.rejects(oxide.search("x"), { name: "OxideError", code: "repository_not_found" });
  });
}
