# OXIDE Stable Agent CLI Design

## Goal

Make `oxide` a predictable local integration surface for coding agents without changing retrieval ranking or adding MCP.

## Contract

- `oxide index [PATH] [--json]` incrementally indexes a repository and reports scan, reuse, removal, symbol, embedding, and failure counts.
- `oxide status [PATH] [--json]` reports repository root, index presence/currentness, embedder compatibility, indexed counts, indexed embedder identity, supported languages, and index schema version.
- `oxide search QUERY [--json]` returns a bounded array of explicit evidence records. Each record has a stable `path#qualified_name` id, repository-relative file, symbol/name/kind/language/location, score, reasons, and compact snippet.
- `oxide context --task TASK [--budget-tokens N] [--json]` returns a bounded deterministic pack using the same evidence record fields plus role and estimated tokens. It omits the Qwen-specific internal `query_used` field.
- Existing non-JSON output and documented JSON top-level forms remain compatible where practical. Search/context stop serializing accidental internal `Symbol` fields.
- Runtime JSON errors use `{ "error": { "code": "...", "message": "..." } }` and a non-zero exit. Human diagnostics go to stderr. Clap usage errors retain exit 2.

## Boundary

`src/service.rs` owns repository discovery/validation, index opening policy, status computation, and calls into existing index/retrieval/context functions. It emits explicit DTOs and typed actionable errors. `src/cli.rs` parses arguments and renders service results; it does not implement retrieval. Retrieval ranking is unchanged; context gains only stable tie ordering.

Read commands never create an index. Status can describe a missing index; search/context fail explicitly with `index_missing`. Indexing remains safe and incremental.

## Freshness

Status compares the indexed file hash map against the current scanner result and file contents, and checks embedding completeness/provider identity. It reports stored embedder identity and a fixed schema version. This is a read-only check; it does not probe or mutate the embedder.

## Provider failures

Indexing keeps the existing `embed_failures` count. Search/context surface provider construction or runtime failure instead of returning a successful-looking empty pack. Lexical-only search does not require a configured semantic provider.

## Verification

Real-binary integration tests cover JSON shape, stdout/stderr, exit codes, index/status/search/context flow, repeat indexing, file deletion/rename, missing/empty repositories, unavailable embedders, malformed invocation, budget enforcement, deterministic ordering, and existing benchmark compatibility. Final verification runs fmt, clippy, all tests, the committed retrieval benchmark, and indexing smoke scenarios.
