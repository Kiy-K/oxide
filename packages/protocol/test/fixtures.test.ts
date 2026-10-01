// Every committed Rust fixture (`fixtures/protocol/`, regenerated only by
// `mise run protocol:fixtures`) must validate against its schema. This is the
// TS half of the drift gate; `tests/protocol_fixtures.rs` keeps the files
// identical to the binary's output.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { z } from "zod";
import {
  ContextResult,
  ErrorEnvelope,
  IndexResult,
  SearchResult,
  StatusResult,
} from "../src/index.ts";
import { fixture, fixtureNames } from "./helpers.ts";

// Fixture name prefix -> the schema for the command that produced it.
const schemas: Record<string, z.ZodType> = {
  status: StatusResult,
  index: IndexResult,
  search: SearchResult,
  context: ContextResult,
  error: ErrorEnvelope,
};

const prefixOf = (name: string) => name.split("-")[0] ?? "";

test("every schema has at least one fixture", () => {
  for (const prefix of Object.keys(schemas)) {
    assert.ok(
      fixtureNames.some((name) => prefixOf(name) === prefix),
      `no ${prefix}-*.json fixture`,
    );
  }
});

for (const name of fixtureNames) {
  test(`fixture ${name} validates`, () => {
    const schema = schemas[prefixOf(name)];
    assert.ok(schema, `no schema for fixture "${name}"`);
    const result = schema.safeParse(fixture(name));
    assert.ok(result.success, `${name}: ${result.error}`);
  });
}

// The edge cases the fixtures were chosen for; if a regeneration stops
// exercising one, this says so instead of silently shrinking coverage.
test("fixtures cover the intended edge cases", () => {
  const noIndex = StatusResult.parse(fixture("status-no-index"));
  assert.equal(noIndex.index_exists, false);
  assert.equal(noIndex.embedder, null);

  const current = StatusResult.parse(fixture("status-current"));
  assert.equal(current.is_current, true);
  assert.equal(typeof current.embedder, "string");

  const stale = StatusResult.parse(fixture("status-stale"));
  assert.equal(stale.base_fresh, false);
  assert.equal(stale.is_current, false);

  const fresh = IndexResult.parse(fixture("index-fresh"));
  assert.ok(fresh.new_symbols > 0);
  const unchanged = IndexResult.parse(fixture("index-unchanged"));
  assert.equal(unchanged.changed_files, 0);
  assert.equal(unchanged.reused_files, fresh.scanned_files);

  const hits = SearchResult.parse(fixture("search-hybrid"));
  assert.ok(hits.some((hit) => hit.reasons.length > 1));
  assert.ok(hits.every((hit) => hit.blast_radius === undefined));

  const blast = SearchResult.parse(fixture("search-blast-radius"));
  assert.ok(blast.some((hit) => (hit.blast_radius?.length ?? 0) > 0));

  assert.deepEqual(SearchResult.parse(fixture("search-empty")), []);

  const pack = ContextResult.parse(fixture("context"));
  assert.ok(new Set(pack.items.map((item) => item.role)).size > 1);
  assert.ok(pack.omitted.length > 0);

  const empty = ContextResult.parse(fixture("context-empty"));
  assert.deepEqual(empty.items, []);
  assert.equal(empty.used_tokens, 0);

  const missing = ErrorEnvelope.parse(fixture("error-index-missing"));
  assert.equal(missing.error.code, "index_missing");
  assert.equal(missing.error.action, "index");
  const notFound = ErrorEnvelope.parse(fixture("error-repository-not-found"));
  assert.equal(notFound.error.code, "repository_not_found");
  assert.equal(notFound.error.action, "stop");
});
