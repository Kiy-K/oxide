# @oxide/protocol

TypeScript types and runtime validation for OXIDE's machine-readable JSON
contracts (#36 T1). Private; not published.

## Owns / does not own

Owns: one zod schema per stable JSON shape, the TypeScript types inferred from
those schemas (`src/index.ts`), and the tests that validate real Rust output.

Does not own: process spawning, SQLite access, editor or MCP code, or any
retrieval logic. The Rust crate is authoritative. When a schema and the
binary disagree, the schema is wrong.

Validation uses zod 4: no dependencies, types inferred from the schemas, and
`looseObject` matches the additive-field policy below. It also lines up with
the MCP TypeScript SDK, whose `@modelcontextprotocol/core` uses zod; MCP v2
accepts any Standard Schema library, which zod 4 implements.

## Schemas and their Rust source

| Schema | Command | Rust type |
|---|---|---|
| `StatusResult` | `oxide status --json` | `service::StatusResult` |
| `SearchResult` / `Evidence` / `BlastItem` | `oxide search --json` (not `--mode literal`) | `Vec<service::Evidence>`, `blast_radius::BlastItem` |
| `ContextResult` / `ContextItem` / `Omitted` | `oxide query --json` | `service::ContextResult`, `service::ContextEvidence`, `context::Omitted` |
| `ErrorEnvelope` / `ErrorAction` | any failing `--json` command (exit 1), MCP `isError` results | `cli::render_json_error`, `service::ErrorAction` |

The MCP `search` and `query` tools return the same shapes, serialized compactly.

Not modeled yet, and passed through unvalidated: `ContextResult.git` (`--git`).
Not covered at all yet: `index`, `review`, `search --mode literal`, `setup` and
the agent commands.

## Fixtures and the drift gate

`fixtures/protocol/*.json` holds the real stdout of the `oxide` binary, run
against a copy of `fixtures/py_repo` with the hashed embedder.
`tests/protocol_fixtures.rs` (Rust, no Node needed) reruns every command and
fails if any byte differs. The only normalization is the temporary
repository path, which becomes `<repo>`.

After an intended change to Rust's JSON output:

```bash
mise run protocol:fixtures   # rewrite fixtures/protocol/ from the binary
mise run verify:ts           # the schemas must still accept them
```

A breaking change (a removed, renamed or retyped field, or a new enum value)
fails `verify:ts` until `src/index.ts` is updated. A Rust change that skips
regeneration fails the Rust tests.

## Compatibility policy

- **Additive fields are accepted and preserved.** Objects are `looseObject`, so
  a field Rust adds later passes through without a release here (SURF-002
  treats new optional fields as non-breaking).
- **Removed, renamed or retyped fields are breaking.** They fail validation.
- **Enums are closed**: `Language`, `SymbolKind`, `Role`, `ErrorAction` and the
  blast-radius `relation`. A new Rust variant is a protocol change.
  `status.supported_languages` lists every language, so a new language reaches
  the fixtures automatically.
- **Error `code` is open.** SURF-002 lets Rust add codes, so any non-empty
  string is accepted. Use `action` for generic handling.
- **Optional and nullable fields follow serde.** `blast_radius` and
  `diagnostics` are omitted when empty (`optional`). `embedder` is always
  present and may be `null` (`nullable`).
- **Prose is never identity.** Treat error `message`, `Omitted.why` and
  `reasons` as display strings and don't match on them.
