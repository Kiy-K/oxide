# Literal search: control-arm baseline

Status: **Control arm shipped** (`oxide search --mode literal`, `src/literal.rs`).
This is the baseline issue #6's Pareto gate measures a future FTS5/trigram
proposal against — it is not itself a verdict on trigram indexing, which
stays out of scope here by design (no regex, no trigram, no FTS5 code).

## What was built

A native, deterministic byte-substring scan over every file OXIDE's ignore
policy would keep (`scanner::scan_repo_text` — the same denylist, `.gitignore`
handling, binary sniffing, and size cap as indexing's `scan_repo`, minus the
"has a recognized language" gate, so READMEs, configs, and other repository
text are searchable too). Exposed identically through:

- CLI: `oxide search QUERY --mode literal [--limit N] [--json]`
- `RepositoryService::search_literal(pattern, limit)`
- MCP: the existing `search` tool's optional `mode: "literal"` argument —
  not a separate tool. `search`'s other mode values (lexical/semantic/
  hybrid) stay unexposed over MCP as before (they only change internal
  ranking weights and share one output shape); `literal` earns a place in
  the same schema specifically because it changes *capability* (no index
  needed) and *output shape* (`{hits, truncated}` instead of ranked
  evidence) — see `SEARCH_MODE_DESCRIPTION` in `src/mcp.rs`. This mirrors
  the CLI's own `search --mode literal`/`search --json` asymmetry: same
  verb, output shape gated by `mode`, rather than inventing an MCP-only
  naming convention.

It needs no index and no embedder: `RepositoryService::search_literal` reads
files straight off disk, so it works identically before and after `oxide
index` has ever run — and never touches `.oxide/index.db` if one already
exists (`tests/literal_search.rs::literal_search_never_touches_an_existing
_index_db`).

## Correctness

- `src/literal.rs`'s own unit tests pin byte-exact matching (including on
  non-UTF-8 files — the pattern is matched as raw bytes; only the returned
  snippet is lossily decoded for display), deterministic truncation
  (accumulation happens in the same sorted `(file, line, column)` order the
  final result is in, so a `build_parallel()` walk's nondeterministic
  arrival order never leaks into which subset survives a limit), and that
  the ignore policy (denylist dirs, `.gitignore`, and — via the walker's own
  `hidden(true)` — dotfiles including `.env*`) is respected.
- `tests/literal_search.rs::matches_ripgrep_over_the_same_file_set` compares
  OXIDE's literal search against `rg -F` restricted to *exactly* the file
  set `scanner::scan_repo_text` returns (fetched directly via
  `oxide::scanner::scan_repo_text`, not re-derived heuristically). Comparing
  against a bare `rg -F` over the whole tree would only measure the two
  tools' differing default ignore policies, not the matcher itself — this
  is why the file set is pinned before comparing.
- `tests/mcp_e2e.rs::search_literal_mode_needs_no_index_and_matches_cli_output`
  asserts the CLI (`--json`) and MCP `search` tool (`mode: "literal"`)
  return byte-identical JSON for the same query on the same repository,
  with no index built — the parity criterion the issue's acceptance list
  asks for. `search_rejects_a_lexical_semantic_or_hybrid_mode_value` pins
  that a `mode` value from the CLI's larger lexical/semantic/hybrid/literal
  set, other than `"literal"`, is rejected rather than silently falling
  through to the default hybrid search.
- UX matrix (`tests/literal_search.rs`): empty pattern is an
  `invalid_configuration` error (not a match-everything scan), no matches is
  an empty bounded result rather than an error, an empty/unindexed
  repository is not an error, quoted regex metacharacters (`.`, `*`, `(`)
  match literally rather than as a pattern, and `--mode bogus` is rejected
  with `literal` listed among the valid choices.

## Zero index/DB/update cost

The control arm's whole value proposition is that it costs *nothing* at
rest and *nothing* to keep current — not just that it can run without an
index, but that it never creates or modifies one:

- `tests/literal_search.rs::literal_search_never_creates_an_oxide_directory`:
  a literal search on a repository with no `.oxide` leaves it that way.
- `tests/literal_search.rs::literal_search_never_touches_an_existing_index_db`:
  on a repository that already has an index, `index.db`'s bytes and the
  full set of files under `.oxide/` (no incidental `-wal`/`-shm`) are
  identical before and after a literal search — the same idiom
  `tests/cli_e2e.rs::read_only_commands_never_modify_index_db_content`
  already pins for `status`/`search`/`context`. Unlike those, which read
  the database through `SqliteStore::open_read_only` and can therefore
  legitimately touch WAL state as any reader does,
  `RepositoryService::search_literal` never opens the database at all, so
  its bar is the stricter "not even that."
- There is consequently no "cold-index cost" or "database growth" line for
  literal search itself in the latency table below — those columns are
  N/A by construction, not measured-and-small. The one index-related
  number in this doc (`oxide index`'s ~5.5s / 70MB on the 800-module repo)
  is reported only as a contrast, not as a cost literal search incurs.

## Latency vs. `rg -F`

Measured with `scripts/literal_search_bench.sh` on `scripts/gen_bench_repo.py`
synthetic repos (this machine, 20 reps, best-effort — not a controlled
benchmark environment; treat as order-of-magnitude, not a precise number).
Pattern: `RetryPolicy`, present across the generated corpus.

| Repo size            | oxide literal p50 / p95 | `rg -F` p50 / p95 (whole tree) |
|-----------------------|------------------------|----------------------------------|
| 200 modules/lang (1,205 files) | 28ms / 37ms | 23ms / 32ms |
| 800 modules/lang (4,805 files) | 46ms / 57ms | 29ms / 36ms |

Both numbers are dominated by process startup and file I/O at this corpus
size, not by the matcher itself — `rg -F` stays roughly 1.3-1.6x faster,
consistent with it being a highly-tuned SIMD substring search (`memchr`)
against OXIDE's straightforward byte-window scan, not evidence of an
algorithmic gap. `rg`'s hit count is uncapped and includes files OXIDE's
denylist excludes (e.g. nothing under `.git`); OXIDE's is capped at
`--limit` (200 here), which is why the two hit counts in the script's raw
output aren't directly comparable — the parity test above, not this script,
is what establishes they agree on file/line/column identity.

For reference, `oxide index` on the 800-module repo (26,415 symbols) took
~5.5s and produced a 70MB `.oxide/index.db` — literal search needs none of
that, which is the whole point of a path that works before a repository has
ever been indexed.

## What this does and doesn't settle

This closes the "control" half of issue #6: a deterministic, tested,
documented literal-search surface exists, needs no index, and has a
measured latency floor to compare against. It does **not** evaluate FTS5 or
trigram indexing — that remains a separate follow-up, gated by the same
Pareto criterion the issue states: adopt only if it beats these numbers (and
this scan's ~0 storage cost) by enough to justify the ~34MB the prior
experiment measured for a trigram index.
