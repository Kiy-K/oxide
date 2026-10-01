/**
 * Runtime schemas for OXIDE's machine-readable JSON contracts, with the
 * TypeScript types inferred from them: one TS source of truth per shape.
 *
 * Rust is authoritative. Each schema names the Rust type that serializes
 * the shape, and `fixtures/protocol/*.json` (real `oxide ... --json`
 * output, kept current by `tests/protocol_fixtures.rs`) must validate.
 *
 * Objects are loose: a field Rust adds later passes through untouched
 * (additive changes are non-breaking, docs/review/api-surface.md SURF-002),
 * while a removed, renamed or retyped field fails validation.
 */
import { z } from "zod";

const count = z.number().int().nonnegative();

/** `symbols::Language` (`serde(rename_all = "lowercase")`). */
export const Language = z.enum([
  "python",
  "typescript",
  "tsx",
  "javascript",
  "rust",
  "go",
  "java",
  "ruby",
  "php",
  "c",
  "cpp",
  "markdown",
]);
export type Language = z.infer<typeof Language>;

/** `symbols::SymbolKind` (`serde(rename_all = "lowercase")`). */
export const SymbolKind = z.enum([
  "module",
  "class",
  "function",
  "method",
  "interface",
  "typealias",
  "enum",
  "constant",
  "import",
]);
export type SymbolKind = z.infer<typeof SymbolKind>;

/** `oxide status --json`: `service::StatusResult`. */
export const StatusResult = z.looseObject({
  root: z.string(),
  index_exists: z.boolean(),
  is_current: z.boolean(),
  embedder_current: z.boolean(),
  base_fresh: z.boolean(),
  pending_embeddings: count,
  files: count,
  symbols: count,
  embeddings: count,
  /** `null` until an index records its embedding provider. */
  embedder: z.string().nullable(),
  supported_languages: z.array(Language),
  schema_version: count,
});
export type StatusResult = z.infer<typeof StatusResult>;

/** `oxide index --json`: `service::IndexResult` (counts for this run). */
export const IndexResult = z.looseObject({
  scanned_files: count,
  changed_files: count,
  reused_files: count,
  removed_files: count,
  new_symbols: count,
  changed_symbols: count,
  deleted_symbols: count,
  embedded_symbols: count,
  reused_embeddings: count,
  embed_failures: count,
  errored_files: count,
  relations_refreshed_symbols: count,
});
export type IndexResult = z.infer<typeof IndexResult>;

/** `blast_radius::BlastItem`; `relation` values are the ones it emits. */
export const BlastItem = z.looseObject({
  id: z.string(),
  file: z.string(),
  qualified_name: z.string(),
  kind: SymbolKind,
  start_line: count,
  end_line: count,
  relation: z.enum(["caller", "implementor", "test", "transitive-caller"]),
  distance: count,
  via: z.string(),
  via_id: z.string(),
});
export type BlastItem = z.infer<typeof BlastItem>;

/**
 * One search hit: `service::Evidence`. `id` is the symbol identity
 * `path#QualifiedName`. `reasons` are display strings in a pinned format
 * (`fixtures/candidate_output/golden.txt`); they are not parsed here.
 */
export const Evidence = z.looseObject({
  id: z.string(),
  file: z.string(),
  qualified_name: z.string(),
  name: z.string(),
  kind: SymbolKind,
  language: Language,
  start_line: count,
  end_line: count,
  score: z.number(),
  reasons: z.array(z.string()),
  snippet: z.string(),
  /** Absent unless `--blast-radius` was requested and this hit was a seed. */
  blast_radius: z.array(BlastItem).optional(),
});
export type Evidence = z.infer<typeof Evidence>;

/** `oxide search --json` (non-literal modes): `Vec<service::Evidence>`. */
export const SearchResult = z.array(Evidence);
export type SearchResult = z.infer<typeof SearchResult>;

/** One `oxide search --mode literal` match: `literal::LiteralHit` (1-based line/column). */
export const LiteralHit = z.looseObject({
  file: z.string(),
  line: count,
  column: count,
  snippet: z.string(),
});
export type LiteralHit = z.infer<typeof LiteralHit>;

/** `oxide search --mode literal --json`: `literal::LiteralSearchResult`. */
export const LiteralSearchResult = z.looseObject({
  hits: z.array(LiteralHit),
  /** More matches existed than were returned. */
  truncated: z.boolean(),
});
export type LiteralSearchResult = z.infer<typeof LiteralSearchResult>;

/** `context::Role`. */
export const Role = z.enum(["primary", "dependency", "test"]);
export type Role = z.infer<typeof Role>;

/** `service::ContextEvidence`: `Evidence` flattened, plus role and token estimate. */
export const ContextItem = Evidence.extend({
  role: Role,
  est_tokens: count,
});
export type ContextItem = z.infer<typeof ContextItem>;

/** `context::Omitted`. `why` is a human-readable reason, not an identifier. */
export const Omitted = z.looseObject({
  id: z.string(),
  why: z.string(),
});
export type Omitted = z.infer<typeof Omitted>;

/**
 * `oxide query --json`: `service::ContextResult`. Its optional `git` object
 * (`--git`) is not modeled yet and passes through unvalidated.
 */
export const ContextResult = z.looseObject({
  task: z.string(),
  budget_tokens: count,
  used_tokens: count,
  items: z.array(ContextItem),
  omitted: z.array(Omitted),
  /** Absent unless an evidence source degraded during the call. */
  diagnostics: z.array(z.string()).optional(),
});
export type ContextResult = z.infer<typeof ContextResult>;

/** `service::ErrorAction::as_str`: what a caller should do about a failure. */
export const ErrorAction = z.enum(["index", "repair", "retry", "fall_back", "stop"]);
export type ErrorAction = z.infer<typeof ErrorAction>;

/**
 * The error envelope printed on stdout by any `--json` command that fails
 * (exit status 1; `cli::render_json_error`), and returned by the MCP tools
 * as an `isError` result (`mcp::service_error_result`).
 *
 * `code` is the stable identity (`service::ErrorCode::as_str` plus a few
 * CLI-only codes). New codes are additive, so any non-empty string is
 * accepted; key generic handling on `action`. `message` is prose for
 * humans: show it, never match on it.
 */
export const ErrorEnvelope = z.looseObject({
  error: z.looseObject({
    code: z.string().min(1),
    action: ErrorAction,
    message: z.string(),
  }),
});
export type ErrorEnvelope = z.infer<typeof ErrorEnvelope>;
