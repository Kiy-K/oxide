// Mutations of real fixtures: breaking drift must be rejected, additive drift
// accepted. Each case clones a fixture, applies one change, and parses.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { z } from "zod";
import { ContextResult, ErrorEnvelope, SearchResult, StatusResult } from "../src/index.ts";
import { fixture } from "./helpers.ts";

type Json = Record<string, unknown>;

function object(value: unknown): Json {
  assert.ok(value !== null && typeof value === "object" && !Array.isArray(value));
  return value as Json;
}

function first(list: unknown): Json {
  assert.ok(Array.isArray(list));
  return object(list[0]);
}

function mutated(name: string, change: (value: unknown) => void): unknown {
  const value = structuredClone(fixture(name));
  change(value);
  return value;
}

function rejects(schema: z.ZodType, value: unknown) {
  assert.equal(schema.safeParse(value).success, false);
}

const blastSeed = (hits: unknown): Json => {
  assert.ok(Array.isArray(hits));
  const hit = hits.map(object).find((h) => Array.isArray(h.blast_radius));
  assert.ok(hit);
  return first(hit.blast_radius);
};

test("rejects a missing required field", () => {
  rejects(
    StatusResult,
    mutated("status-current", (s) => {
      delete object(s).index_exists;
    }),
  );
  rejects(
    SearchResult,
    mutated("search-hybrid", (hits) => {
      delete first(hits).qualified_name;
    }),
  );
  rejects(
    ErrorEnvelope,
    mutated("error-index-missing", (e) => {
      delete object(object(e).error).code;
    }),
  );
});

test("rejects a nullable field turned optional", () => {
  rejects(
    StatusResult,
    mutated("status-no-index", (s) => {
      delete object(s).embedder;
    }),
  );
});

test("rejects a wrong primitive type", () => {
  rejects(
    SearchResult,
    mutated("search-hybrid", (hits) => {
      first(hits).start_line = "1";
    }),
  );
  rejects(
    StatusResult,
    mutated("status-current", (s) => {
      object(s).files = -1;
    }),
  );
  rejects(
    ContextResult,
    mutated("context", (pack) => {
      object(pack).used_tokens = 1.5;
    }),
  );
});

test("rejects an unknown enum value", () => {
  rejects(
    SearchResult,
    mutated("search-hybrid", (hits) => {
      first(hits).kind = "struct";
    }),
  );
  rejects(
    StatusResult,
    mutated("status-current", (s) => {
      object(s).supported_languages = ["python", "cobol"];
    }),
  );
  rejects(
    ContextResult,
    mutated("context", (pack) => {
      first(object(pack).items).role = "secondary";
    }),
  );
  rejects(
    ErrorEnvelope,
    mutated("error-index-missing", (e) => {
      object(object(e).error).action = "panic";
    }),
  );
});

test("rejects a malformed nested evidence object", () => {
  rejects(
    SearchResult,
    mutated("search-blast-radius", (hits) => {
      blastSeed(hits).relation = "callee";
    }),
  );
  rejects(
    SearchResult,
    mutated("search-blast-radius", (hits) => {
      delete blastSeed(hits).via_id;
    }),
  );
  rejects(
    ContextResult,
    mutated("context", (pack) => {
      first(object(pack).omitted).why = 7;
    }),
  );
  rejects(
    ContextResult,
    mutated("context", (pack) => {
      first(object(pack).items).reasons = "lexical=1.0";
    }),
  );
});

test("accepts and preserves additive fields", () => {
  const status = StatusResult.parse(
    mutated("status-current", (s) => {
      object(s).future_field = { nested: true };
    }),
  );
  assert.deepEqual(status.future_field, { nested: true });

  const pack = ContextResult.parse(
    mutated("context", (p) => {
      object(p).git = { range: "HEAD", changed_files: [] };
      first(object(p).items).future_score = 1;
    }),
  );
  assert.deepEqual(pack.git, { range: "HEAD", changed_files: [] });
  assert.equal(pack.items[0]?.future_score, 1);
});

test("accepts an error code it does not know yet", () => {
  const envelope = ErrorEnvelope.parse(
    mutated("error-index-missing", (e) => {
      object(object(e).error).code = "future_failure";
    }),
  );
  assert.equal(envelope.error.code, "future_failure");
});
