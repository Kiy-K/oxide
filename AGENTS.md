# AGENTS.md

OXIDE: local incremental code index + hybrid retrieval (Rust, single crate).
Full docs: `README.md`; context-engineering rationale: `docs/context-engineering-notes.md`.

**Reviewing a change (Codex, Claude, or human)?** Read `docs/review/README.md`
first — it encodes OXIDE-specific invariants, severities, and evidence
standards so review comments catch real regressions instead of generic
style notes. The "load-bearing invariants" below are BLOCKER-severity under
that policy.

## Commands

`mise.toml` pins every non-Rust dev tool (Python 3.11, uv, shellcheck, jq,
cargo-llvm-cov) and wraps the checks below in tasks matching CI's current
commands step-for-step, so there is one place either can drift from once CI
itself is migrated to call `mise run` directly (not done yet — pending
verification on a real runner; CI still runs the raw commands below). See
`mise.toml`'s own comments for why Rust itself stays pinned only in
`rust-toolchain.toml`. With
[mise](https://mise.jdx.dev/installing-mise.html) installed (prefer a system
package, e.g. `pacman -S mise`/`brew install mise`, over piping an installer
script):

```bash
mise run bootstrap  # installs the pinned Rust toolchain/components + mise tools
mise run lint       # cargo fmt --check + clippy -D warnings
mise run test       # full unit + integration suite
mise run bench      # release build + the canonical fixture benchmark
mise run verify     # lint, lint/test --no-default-features, test, bench, installer checks, in order
```

Without mise, the same checks run directly — this is what CI's `quality`/
`test`/`no-default-features`/`retrieval-gate` jobs currently run:

```bash
cargo test -j 2                 # all tests; keep -j 2 (laptop)
cargo test -j 2 --lib retrieval # one module
RUST_TEST_THREADS=2 cargo test -j 2   # if integration tests contend
cargo fmt && cargo clippy -j 2 --all-targets   # clippy must be warning-free
cargo build --release -j 2      # CLI used by eval scripts lives here
./target/release/oxide eval --config fixtures/benchmark.json   # committed fixture benchmark
scripts/perf.sh 200             # perf harness on synthetic repo (build release first)
```

Order matters only for commits: fmt → clippy → test → benchmark gate.

`tests/benchmark_gate.rs` is semantic, not mechanical: it fails unless hybrid
retrieval ≥ vector-only recall@5 on `fixtures/benchmark.json`. If a ranking
change fails it, fix the ranking or honestly re-baseline both numbers.

## Load-bearing invariants (breaking these looks fine until a benchmark fails)

- Symbol ids = `FNV1a(file + \0 + qualified_name)` and are persisted. They make
  incremental re-embedding work (unchanged content_hash ⇒ embedding reused).
  Never change id composition casually. **Java is the one language whose
  `qualified_name` carries a signature** (`Store.get(String,String)`,
  `tags.rs::java_signature`) — because Java overloading is idiomatic and
  without it `parse_file_with`'s first-wins dedup silently drops every
  overload but one. That was done by changing Java's *name*, never the id
  *formula*, which is exactly why it cost no cross-language re-embed: the
  five pre-existing conformance goldens are byte-identical, and
  `java_overloads_keep_distinct_ids_without_touching_other_languages`
  asserts no other language's names ever contain `(`. Any future
  overload-bearing language (C#, C++) must follow the same route. The
  normalization is erasure-shaped — generics dropped, package qualifiers
  reduced to the last segment, varargs turned into arrays, parameter names
  and annotations excluded — so a parameter rename never re-embeds and two
  spellings of the same type never split a symbol.
- JavaScript/JSX has **no grammar and no `.scm` of its own**: `Language::
  JavaScript` runs on the TSX grammar with the TypeScript tags/locals query
  and the TSX callers/implementors queries (`languages::JAVASCRIPT_PROFILE`).
  TSX is a syntactic superset of JavaScript and resolves `<` the way a
  `.jsx` file does; 261/261 real `.js` files across tailwindcss and
  openlibrary parse with zero ERROR/MISSING nodes. Forking those queries
  into `javascript_*.scm` copies would reintroduce exactly the drift
  `TSX_CALLERS_SRC`'s concatenation already exists to prevent. The one
  JavaScript-specific line anywhere is CommonJS `require()` in
  `collect_meta`, which the shared TypeScript import arm cannot see.
- All ids/hashes cross SQLite as `as i64` bit-casts (u64 → i64 → u64). The
  casts look like bugs; they are not.
- Duplicate qualified names per file are deduped (first wins) in
  `parser.rs::parse_file`. Real repos have overloads/conditional defs; without
  this, indexing dies on `UNIQUE constraint failed: symbols.id`.
- Retrieval expansion must never displace direct hits: direct results keep
  their pre-expansion scores; expansion-only items are appended after. The
  benchmark gate depends on this invariant (`src/retrieval/engine.rs`).
- Retrieval is **candidate-first** (docs/sqlite-request-path/README.md):
  `RetrievalEngine::search` scores BM25 over postings and streams the
  embedding rows through a bounded top-200 heap (`semantic_top_k`), then
  hydrates only the fused candidates via `symbols_by_ids`. Nothing on the
  request path may call `all_symbols`/`all_embeddings` — the one exception
  is `SymbolSnapshot`, loaded lazily and only when structural expansion
  needs the whole corpus (`RelationGraph::related_tests` scans every
  symbol by contract), or injected by `oxide mcp`'s process cache — and
  loaded *lean* (next bullet).
  `search_hydrates_only_candidates_unless_expansion_needs_the_corpus`
  pins this with a counting store. The heap's comparator is
  `cmp_score_id`, the same total order the old full sort used, so the
  retained set and its order are identical; the dot product stays a
  sequential `f32` sum so scores are bit-identical
  (`streaming_semantic_scan_matches_materialized_scan_exactly`).
  `FUSION_CANDIDATE_LIMIT` is a ranking input, not a tuning knob: a
  different depth changes RRF's inputs.
- `SymbolSnapshot` is **lean** (`IndexBackend::all_symbols_lean`,
  docs/retrieval-profile/corpus-load-baseline/lean-snapshot/): every
  symbol is `Completeness::Partial` — `imports` empty, `references` only
  on test symbols (`symbols::is_test_symbol`, the one predicate
  `related_tests` also uses) — because that is all `RelationGraph` reads
  of a non-seed symbol. It cut one-shot expanded requests 16–27 % and
  peak RSS 23–32 %. A partial symbol must never pass for a complete
  one: every symbol that becomes a `neighbors()` seed or leaves as output
  goes through `retrieval::complete_symbols` (one bounded
  `symbols_by_ids` read, keeping `calls`/`bases`) — search's strong seeds
  and returned hits, `context.rs`'s candidates, the coordinator's
  git-changed seeds, `review`'s seeds and `related`. Backstops, all loud:
  `Completeness` is skipped when complete (JSON byte-identical) and its
  `Serialize` always errors; `RelationGraph::neighbors` and
  `LexicalIndex::build` assert completeness; the counting store bounds
  completion reads. A new consumer of snapshot symbols completes them
  first. When BM25 falls back to memory (`retrieval::lexical_persisted`
  false) every snapshot loader returns complete symbols instead, so the
  fallback's single load is unchanged. `lean_snapshot_output_matches_the_
  complete_corpus_oracle` pins search/context/`--git`/`--blast-radius`/
  review/MCP-cache/fallback output against a complete-corpus store.
- `RetrievalEngine::search` runs `embed_query` on its own OS thread via
  plain `std::thread::scope` — not a tokio task — while BM25 runs on the
  calling thread, then the vector scan follows on the calling thread once
  the query vector is back (both stages read through `store: &dyn
  IndexBackend`, and `SqliteStore` is not `Sync`). This is deliberate:
  `oxide context`/`oxide search` from the CLI run fully synchronously with
  no tokio runtime at all (`cli/commands/mcp.rs::run_mcp`'s own comment says so), while
  MCP already runs the whole service call inside `spawn_blocking`.
  `std::thread::scope` is the one primitive that works identically from
  both without adding a Cargo.toml tokio feature. The spawned closure
  captures only `self.embedder`, never `self` — `self: &RetrievalEngine`
  is not `Send` (it holds the store reference and `OnceCell`s), even
  though the closure never touches those fields.
- `meta.index_generation` is bumped inside **every** write transaction
  (`storage.rs::bump_generation`; `tests/index_generation.rs` enumerates
  the nine paths) and `meta.index_id` is a per-database random identity
  set on writer open. Together they key `oxide mcp`'s process cache
  (`service.rs::ProcessCache`: symbol snapshot, row counts, embedder):
  equal `(index_id, index_generation, schema/extraction/embedding/lexical
  keys)` read inside a request's own read snapshot ⇒ identical content.
  A new write path that forgets the bump lets a cached snapshot outlive
  the content it was built from; the generation alone is not unique
  across a deleted-and-rebuilt `.oxide`, which is what `index_id` is for.
  The cache entry also holds a `RelationIndex` (`relations.rs`) built
  over that snapshot — a lifetime-free, hash-keyed, verify-on-read index
  the `RelationGraph` is a view over — so the two are invalidated
  together by the same key. `RetrievalEngine::relation_graph()` uses the
  cached index only when the engine's snapshot *is* the cached one
  (pointer check) and builds a fresh index otherwise; never pair an
  index with a snapshot it was not built from
  (docs/retrieval-profile/README.md §6.1).
  `PRAGMA data_version` was deliberately not used: it is only comparable
  between reads on the same connection, and every request opens its own.
- `update_base` runs inside `IndexBackend::begin/end_bulk_writes`, which
  raises `wal_autocheckpoint` to `BULK_WAL_AUTOCHECKPOINT_PAGES` (16 MB)
  for the full-corpus pass and restores SQLite's 4 MB default on every
  exit path. Disabling checkpoints outright let the WAL reach 1.16 GB for
  a 46 MB database; `synchronous=NORMAL` and `mmap_size` were measured
  and not shipped (docs/sqlite-request-path/README.md §5). `oxide watch`'s
  per-batch `update_base_for_files` writes keep the default.
- `tests/query_plans.rs` pins `EXPLAIN QUERY PLAN` for every request-path
  statement with and without `ANALYZE` statistics. A statement that starts
  scanning `symbols` or `lexical_postings` fails that test on purpose.
  `idx_embeddings_symbol` exists for exactly one statement — `COUNT(*)
  FROM embeddings`, which `validate_index` runs on every request and which
  otherwise walks the blob-bearing table b-tree (7.5 ms at 15k symbols,
  14–20% of a `--no-expand` search) — and the vector scan must keep
  planning as a plain `SCAN embeddings` next to it. `all_symbols` keeps
  its SQL `ORDER BY file, start_line`: measured, the ordered step is no
  slower than a rowid scan plus a Rust sort (the sorter's cost is the
  overflow-page reads a plain scan only defers), and its tie order is
  `(file, rowid)`, which downstream corpus-order consumers depend on
  (docs/retrieval-profile/README.md §2.1).
- `RetrievalMode` (`Fast`/`Balanced`/`Quality`, `retrieval/options.rs`) only gates
  the *bounded structural-relation expansion* stage in `context.rs`'s own
  expansion loop — never the always-on lexical+semantic stage, and never
  `RetrievalEngine::search`'s own RelationGraph expansion (`opts.expand`)
  except that `Fast` also skips it there. An unconfigured caller always
  resolves to `Balanced` (`RetrievalMode::resolve(None)`, checked before
  `$OXIDE_RETRIEVAL_MODE`) — config may only raise or lower that default, per
  the same precedence `open_embedder` already uses for `$OXIDE_EMBED_URL`.
  The bounded structural-relation expansion's file scope is the union of
  the seed pool's own files (capped), matching `RelationGraph::callers_of`'s
  repo-wide result being filtered down to exactly that scope before use
  (`structural_relations.rs`/`docs/precomputed-relations-migration/README.md`)
  — not a per-seed RelationGraph-neighbor lookup, which would only rescan
  files a name-matching heuristic already flagged and add little new
  signal. This means a caller in a file the seed search didn't
  independently surface is invisible to it; that's a real ceiling, not a
  bug (see docs/retrieval-coordinator/README.md).
- Lexical docs include symbol *bodies* at weight 1 (names/signatures weight 4).
  Body tokens were added because gold-context evals showed bugfix targets hide
  behind local identifiers. Don't remove for "cleanup".
- `LexicalIndex::build` reads bodies from the repo root recorded in index meta
  (`get_meta("root")`) — engine construction needs an indexed store, not just
  symbols.
- An embedding's cache-invalidation key must always cover
  `embeddings::symbol_embed_text(symbol)` exactly — never a proxy for it. The
  key is the persisted `symbols.content_hash`, which `update_index` rewrites
  for **every** symbol once `references` are resolved:
  `embedding_input_hash(parser_hash, &symbol_embed_text(s))`. The parser hash
  alone is a proxy — a concrete symbol's covers only its span, the module
  symbol's only imports + first line — while `symbol_embed_text` also carries
  file-level `imports` and post-parse `references`, so an import-only edit or
  a new same-file definition an untouched body names used to leave a stale
  vector that a clean rebuild would not. Keeping the parser hash in the key
  preserves "span edit ⇒ reprocess" and the empty-file module's full-source
  hash, without which comment-only files silently stop reporting edits
  (`tests/embedding_staleness.rs` pins all of these). Existing indexes are
  not force-migrated: rows for files not reparsed since keep their old keys
  and vectors until those files change or `oxide index -a` runs.
- Cross-file, same-run reference staleness is a known, accepted gap: if file A
  adds a name that a symbol in unrelated, already-reparsed file B's body
  happens to textually match, B's `references` can lag until B itself is next
  reparsed. Fixing it needs real dependency tracking (a graph), which is out
  of scope by design (see `docs/agent-usage-policy.md`-adjacent "no new
  architecture" note in the Phase 1.1 report). Don't "fix" this with a
  targeted patch; it needs an architecture discussion first.
- `SqliteStore::open_read_only` must stay a plain `SQLITE_OPEN_READ_ONLY`
  connection, never `immutable=1`: that flag disables WAL/locking
  consistency checks and SQLite's own docs call it unsafe when the file can
  change concurrently, which `index.db` always can (`oxide index` runs from
  any process at any time). The accepted contract: reads never modify
  `index.db` content, but may create/touch the writer's `-wal`/`-shm` files
  like any WAL reader (`tests/cli_e2e.rs::read_only_commands_never_modify_index_db_content`).
- `update_index`'s closing meta writes (root/embedder/dim/schema_version/
  extraction_version) must land as one atomic transaction
  (`IndexBackend::set_meta_all`), never as separate statements. A process
  killed between separate writes could leave `root` set without
  `schema_version`, and `validate_index`'s "missing schema_version means a
  pre-versioning legacy index" fallback would then wave a torn, incomplete
  index through as healthy (`tests/interrupted_index_recovery.rs`).
- A provider switch clears vectors and writes the in-flight fingerprint to
  `embedding_migration` as ONE transaction
  (`IndexBackend::begin_embedding_migration`), and retires that key inside
  the same `set_meta_all` that publishes the new identity. The atomicity only
  works in that order: because the marker cannot exist unless the table was
  emptied in the same transaction, "marker == the provider I am about to
  use" proves every surviving row is that provider's. Setting the marker
  before clearing proves nothing. That proof only extends across concurrent
  `oxide index` processes because every embedding write and the closing
  publish re-read the marker inside their own transaction and fail unless it
  still holds the writer's space (`ensure_migration_marker`) — otherwise a
  second run's migration would empty the table under the first, which would
  keep appending alongside it. Still open by design: a run whose
  compatibility check ran before another run's migration finished, and whose
  first write lands after it, sees an empty marker at both moments; closing
  that needs run-level writer serialization, which embedding's minutes-long
  runtime rules out. `incompatible_stored_space` is the single
  decision point for both `update_embeddings` and `pending_embedding_count`
  (which AGENTS-era comments only *asked* not to diverge), and treats the
  marker as outranking `embedding_fingerprint`, which in turn outranks the
  legacy `embedder`+`dim` pair; a present-but-unparseable value at any tier
  is incompatible and must never fail open into the weaker tier below.
  `validate_index` refuses semantic — never lexical — reads while the marker
  is set (`tests/provider_migration_recovery.rs`).
- Foreign keys ARE enforced, and OXIDE depends on it. `replace_file` and
  `remove_files` delete only from `symbols` and rely on `ON DELETE CASCADE`
  to take `embeddings` and `symbol_relations` with them — which is why
  `replace_file` snapshots the embeddings it means to keep first. SQLite's
  own default is OFF; this held for a long time only because
  `libsqlite3-sys` compiles its bundled amalgamation with
  `-DSQLITE_DEFAULT_FOREIGN_KEYS=1`, so a dependency bump or a system
  SQLite would have silently stranded a row per deleted symbol. `open` now
  issues `PRAGMA foreign_keys = ON` explicitly (before any transaction — it
  is a no-op inside one), and `idx_symbol_relations_symbol_id` /
  `idx_lexical_postings_symbol` exist to keep those cascades index-driven
  rather than full scans. Don't reinstate manual orphan sweeps; they were
  no-ops that scanned both tables in full.
- The persisted BM25 index (`lexical_postings`/`lexical_docs`, written in
  `replace_file`'s transaction) is usable **only** when
  `meta.lexical_index_version` exactly equals `LEXICAL_INDEX_VERSION`. That
  key is published once, at the end of a completed full-corpus
  `update_base`; absence means pre-feature or interrupted, a different
  value means written under different tokenizer/weight rules, and both make
  readers fall back to `LexicalIndex::build` in memory. Table existence and
  row count prove nothing — a backfill killed halfway leaves covered files
  whose `content_hash` already matches, so no incremental run would ever
  revisit them. Scoring is shared (`lexical::score`) between the two
  sources, so the fallback is slow-but-bit-identical, never wrong;
  `tests/lexical_persistence.rs` pins the exact `f32` bits, and
  `oxide eval --config fixtures/benchmark.json` is byte-identical to the
  pre-persistence output. `bm25()` itself is unusable here — it hardcodes
  k1=1.2 against OXIDE's 1.5, uses a different IDF and document-length
  definition, and cannot return the term-coverage evidence fusion consumes
  (`docs/storage-backend-eval/enhanced-sqlite.md`).
- `SqliteStore::open_read_only` holds one deferred WAL read transaction for
  the life of the store, so a request's metadata validation and its later
  vector load see the same snapshot; in autocommit a concurrent `oxide
  index` finishing a provider switch between the two let a request approve
  the old identity and then score the new rows. Safe only because every
  reader is request-scoped — a long-lived one would pin the WAL against
  checkpointing.

## Embeddings / providers

- Provider selection: explicit `--embedder URL` > `$OXIDE_EMBED_URL` >
  `$OXIDE_EMBED_NATIVE` > `DEFAULT_NATIVE_PROFILE` (`arctic-embed-xs-q`).
  A configured remote provider (Voyage/Jina/OpenAI-compatible via
  `$OXIDE_EMBED_PROVIDER`+API key, or `oxide setup`'s saved config gated on
  `remote_consent_ack`) resolves between the explicit-URL tier and
  `$OXIDE_EMBED_NATIVE` — an unconfigured environment falls through
  untouched to the native/hashed tiers exactly as before remote providers
  existed.
  **The default is no longer offline** — an unconfigured `oxide index` loads
  real ONNX weights through fastembed and downloads ~23MB on first use.
  `OXIDE_EMBED_NATIVE=hashed` (`OFFLINE_PROFILE`) opts back out to
  `HashedEmbedder`; so does building `--no-default-features`. The benchmark
  gate is unaffected either way — `src/eval.rs` constructs `HashedEmbedder`
  directly and never calls `open_embedder`.
- `open_embedder` and `configured_provider_name` must resolve the SAME
  provider for the same environment, which is why both go through
  `resolve_native_profile`. If they diverge, `oxide status` reports
  `embedder_current: false` against a current index and `validate_index`
  fires on an embedding space that never changed. A missing model is an
  error, never a silent downgrade to hashed: they are different spaces, and
  swapping them quietly would wipe every stored vector on the next run.
- Provider identity = `http:{model}@{endpoint}` where model comes from
  `$OXIDE_EMBED_MODEL`. Switching the served GGUF quant WITHOUT changing that
  label silently keeps stale, incomparable vectors. Index meta detects the
  change and wipes embeddings — but only if the label differs.
- Dynamically quantized native profiles (fastembed `*Q` Arctic/MiniLM,
  including the default `arctic-embed-xs-q`) compute their int8 activation
  range per batch tensor, so a text's vector depends on its batch-mates
  (min cosine 0.9975 alone vs in a 7-text batch). `NativeEmbedder` therefore
  embeds one text per ONNX call for those profiles (`batch_invariant`), so
  `update_embeddings`' <8-symbol `embed_documents` path and its per-text
  worker path write the same vectors as a clean rebuild
  (`dynamic_quant_batch_matches_single_text`, `#[ignore]`: needs the model).
  Never batch a Dynamic-quantization model for throughput. Existing indexes
  are not force-migrated: vectors an earlier small update wrote in a batch
  stay until their file changes or `oxide index -e`/`-a` runs (the
  canonical single-text space never moved, so no fingerprint bump).
- `NativeEmbedder` runs an ONNX session pool sized by `$OXIDE_EMBED_SESSIONS`
  (`auto` default: `auto_embed_sessions` = one per 4 cores, ≤4, extras ≤¼ of
  available memory incl. cgroup v2 limit, 1 for models > `AUTO_MAX_SESSION_MB`;
  `1` = the pre-pool single session on every core). Extra sessions are
  created lazily and only after `POOL_GROWTH_AFTER_DOCUMENTS` (256) document
  embeds (per process, cumulative — a long `oxide watch` does grow), so
  queries/MCP and a small `oxide index` update never load them (measured: a
  30-symbol update got *slower* when every concurrency spike grew the pool).
  With N>1, slot 0 also runs on cores/N threads — only `1` (or `auto` on
  <8 cores) is the pre-pool thread layout. `available_memory_mb` reads
  cgroup v2 `memory.max` of the process's own cgroup only.
  Sessions split cores (`intra_threads = cores / N`) to avoid
  oversubscription. The pool must stay a pure throughput change: vectors,
  `name()` and `fingerprint()` are identical to one session
  (`session_pool_matches_single_session_bit_for_bit`, `#[ignore]`, int8 +
  fp32), and dynamic-int8 still embeds one text per call. Invalid values
  and model-load failures surface as `embedder_unavailable` with the cause
  in both `--json` and the human CLI.
- HTTP failures return empty vectors by design; the indexer skips and counts
  them (`embed_failures`), retrieval ignores length-mismatched ones.
- Start/stop the local llama.cpp server with `scripts/embedder.sh start|stop`
  (~0.3 GB RSS with the capped profile; Q4_K_M third-party quants are broken,
  stick to official Q8_0).

## Terminal output and telemetry

- `src/term.rs` is the only module that produces ANSI escapes. Human
  renderers take a `Paint` (built once per stream from `--color`,
  `NO_COLOR`, `TERM=dumb`, and isatty) and never write escapes themselves;
  the `--json` paths and `mcp.rs` never touch a `Paint` at all, which is
  what keeps the machine surfaces byte-clean. Indexing progress is a
  `ProgressSink` (`index.rs`) fed to `index_staged` — `begin(stage,
  total)` carries the item count when it is known up front, so the sink
  picks a determinate bar vs. a spinner without guessing; only the CLI
  installs a drawing sink, on stderr — a cliclack step (`◒ … ◇ …`, one
  printed line per finished stage, `✗` on error) when stderr is a real
  terminal (`TERM` set and not `dumb`), one plain line per stage
  otherwise, and the plain path never constructs a widget at all.
  `term.rs` is the only module that names `cliclack` or `console`: it
  installs `OxideTheme` (drops cliclack's `│` guide line, elapsed clock,
  and two-space gutter) and pushes `Paint`'s decision into
  `console::set_colors_enabled{,_stderr}` so `--color`/`NO_COLOR` govern
  cliclack's styling too — there is no separate direct `indicatif`
  dependency because `cliclack::ProgressBar` already wraps it. Stage
  lines advance by full-width padding + terminal wrap (indicatif's
  drawing model), which is why a zero-size pty (`script … /dev/null`
  without `stty rows/cols`) shows them collapsed onto one line — a
  harness artifact, not a bug. `tests/terminal_output.rs` pins the matrix.
- The `oxide index` result block is context-sensitive (`cli/render/index.rs::
  print_index_summary`): `Indexed`/`Reindexed` (fresh store or `-a`,
  whole-corpus counts from two `#[serde(skip)]` presentation fields on
  `IndexResult`, plus a `Done!` footer), `Updated` (files touched +
  symbols new/changed/deleted), `Refreshed` (`-e`/`-g` on a clean tree),
  `Up to date` (one line, no duration). Those skipped fields never reach
  `--json`; `tests/cli_surface.rs` pins the JSON shape.
- Telemetry is Sentry panic reporting and nothing else, off unless
  `OXIDE_TELEMETRY` opts in; `src/telemetry.rs` + `TELEMETRY.md` are the
  contract and `tests/telemetry.rs` watches the wire. Don't add any other
  network call outside the embedding provider path.

## JSON output contracts

`oxide search/context/review --json` feed coding agents. Pack items and search
hits are serde-**flattened**: fields like `file`, `qualified_name`,
`start_line` sit at the top level — there is no nested `"symbol"` key. Symbol
identity everywhere is `path#QualifiedName`.

## Eval harnesses (eval-agent/, scripts/agent_eval/)

- `eval-agent/.venv` is Python **3.11** (`tree-sitter-languages` has no wheels
  ≥3.12); recreate with `uv venv --python 3.11`.
- The ContextBench evaluator is cloned to `eval-agent/third_party/ContextBench`
  (gitignored) on first run of `scripts/agent_eval/contextbench_run.py`.
- Tier A (`contextbench_run.py`) scores retrieval vs human gold contexts;
  results append to `eval-agent/results/cb_results.jsonl` — resumable, keyed by
  (task, condition). Summarize with `summarize_cb.py`.
- Tier B (`tierb_agent_run.py`) runs headless `opencode` per condition. It pins
  `$PWD` to the task-repo copy because opencode trusts PWD over getcwd().
- Long background runs: launch via a script using `setsid ... &` — plain
  backgrounded shells die with the parent. When matching processes, prefer
  `pgrep -fa` + kill-by-PID; `pkill -f somepattern` matches your own command
  line and kills your own shell.

## Repo layout facts

- Single crate: bin `src/main.rs` + lib; modules wired in `src/lib.rs`.
  Language support = add a `LanguageProfile` + `.scm` queries and register
  in `src/parser.rs` (currently python, typescript/tsx, javascript/jsx,
  rust, go, java, ruby, php, c, c++ — see
  `docs/language-support/README.md` for the coverage matrix, per-language
  performance, and what each language still misses). Behavior per language
  is pinned by `tests/language_conformance.rs`'s committed goldens under
  `fixtures/conformance/`; regenerate with `UPDATE_GOLDEN=1` and read the
  diff — a golden that changes without an intended cause is the alarm.
  Java was evaluated and deliberately not added: `docs/java-feasibility/`.
- `parser.rs::extractor_for()` (the default) routes through
  `src/languages/tags.rs`'s generic `TagsExtractor`: a `LanguageProfile`
  (grammar + `queries/*_tags.scm` + `queries/*_locals.scm`) feeds the official
  `tree-sitter-tags` crate, and OXIDE reconstructs parent/containment and
  Python's method-vs-function split itself via byte-range nesting over the
  flat tag list (tags carry no parent info at all). Adding a language is
  meant to mostly be grammar + `.scm` + normalization tests, not a new
  procedural extractor. The original handwritten, per-language AST-walking
  extractors (`languages/python.rs`, `languages/typescript.rs`) are **gone**,
  deleted once the one capability they were retained for —
  decorator-inclusive spans (`@app.route`, `@Injectable()`) — was closed on
  the tags path by `tags.rs::decorator_extended_start`: `collect_meta`'s
  existing single walk also collects `decorator` byte ranges, and a
  definition's span is widened back over any decorator separated from it by
  whitespace only. Done in Rust rather than as `.scm` patterns because the
  two grammars disagree about where a decorator lives (Python wraps the
  definition in `decorated_definition`; TypeScript hangs it off
  `export_statement` for a decorated exported class and off the class body
  for a decorated method), so the query-level fix needs a pattern per
  grammar shape per language *plus* a Rust rule collapsing the resulting
  outer/inner twin definitions. Two consequences worth knowing: a
  decorator's own call now attributes to the decorated symbol rather than
  the file's module symbol, and `structural_relations` matches a base clause
  to the *innermost containing* Class/Interface rather than an exact start
  line, since the symbol's span no longer starts where the class node does.
  Adopting
  `tree-sitter-tags` required bumping `tree-sitter` 0.24→0.27 and
  `tree-sitter-python` 0.23→0.25 (a `links = "tree-sitter"` native-lib crate
  forces one version across the graph); `tree-sitter-typescript` needed no
  bump. See `docs/treesitter-tags-parity/` for the parity evidence.
- Symbol-anchored structural queries (implementors, AST-precise callers)
  are **precomputed at index time**, not answered by a live query-time AST
  scan — `src/structural.rs` (an `ast-grep-core` adapter) and
  `TreeSitterStructuralProvider` (a query-time Tree-sitter-query adapter)
  both existed at points in this project's history and are both gone;
  `ast-grep-core` is no longer a dependency at all. The current pipeline:
  `structural_relations::compute_file_relations` runs inside
  `index::update_index`'s existing per-file loop (same place
  `extract_references` runs — reuses that loop's already-open source and
  already-parsed symbols, adding one extra `tree_sitter::Query` pass per
  reparsed file via `tree_sitter_structural.rs`'s `all_calls_in_file`/
  `all_bases_in_file`), writes `(symbol_id, calls, bases)` to a
  `symbol_relations` SQLite side table (`IndexBackend::put_symbol_relations_batch`,
  one transaction per reparsed file — **every** symbol in that file gets an
  entry, even an empty one, which is what clears a stale relation after an
  edit removes a symbol's last call/base), and `context.rs`'s bounded
  expansion reads it back via `RelationGraph::callers_of`/`implementors_of`
  (`relations.rs`, two `OnceCell`-lazy reverse indexes over `Symbol.calls`/
  `bases` — `RelationGraph::build()` itself does zero extra work whether or
  not those fields are populated). Any caller of `callers_of`/
  `implementors_of` MUST intersect the result with an explicit bounded file
  scope before it reaches context output — the lookup itself is repo-wide
  by construction and a 902-file synthetic-repo measurement showed 60x more
  results unfiltered than the same lookup scoped to a realistic seed pool;
  `context.rs` enforces this the same way it always has, with a
  `scope_files` filter built from the seed pool's own files (capped),
  applied to the `callers_of` result before use — see
  `docs/review/structural-and-language.md`'s LANG-001. **The one sanctioned
  exception is `blast_radius.rs`**, which must reach files the seed search
  did *not* surface (that is the feature), and therefore carries its own
  explicit bound instead: hard caps on seeds, per-seed members, total
  members, distinct files, and the single transitive hop
  (`config.rs`'s `BLAST_RADIUS_*`), all enforced in one accumulator so no
  traversal branch can skip one. A third consumer needs the same treatment:
  an explicit bound of its own, not an unfiltered repo-wide result.
  Attribution (mapping
  a raw call-site/base-clause line back to the `Symbol` it belongs to,
  `structural_relations::enclosing`) has three known ties, each found
  empirically and fixed with a specific tie-break, each pinned by its own
  regression test — see LANG-002 in the same review doc before touching
  `enclosing()` or `compute_file_relations()`. `calls`/`bases` are bare
  names, same heuristic tier (identifier-name intersection, no scope
  analysis) as `Symbol.references`/the `uses` relation — not more precise
  about *which* callee a name resolves to, only about *what counts as a
  call* (an AST call-expression, not `references`'s token match). See
  `docs/precomputed-relations-migration/README.md` for the migration
  evidence and final numbers, `docs/precomputed-structural-relations/README.md`
  and `docs/treesitter-structural-eval/README.md` for the two experiments
  that preceded it, and `docs/astgrep-structural-search/`/
  `docs/astgrep-hardening/` for the original (now superseded) ast-grep
  spike and hardening pass.
- Storage is SQLite behind the small `IndexBackend` trait (`src/index.rs`);
  DB lives at `<repo>/.oxide/index.db`. The backend question is **closed**:
  SurrealDB and Turso were evaluated and rejected, and Enhanced SQLite was
  built and measured (`docs/storage-backend-eval/`). Enhancements are
  limited to features already bundled with the pinned `rusqlite 0.32`;
  proposing a different backend means a fresh evaluation round, not a
  patch. Still open by design, and not settled by any of that: the vector
  path is a brute-force scan, comfortable to roughly 50k symbols — the
  question Zvec stays frozen against.
- `fixtures/py_repo` and `fixtures/ts_repo` are committed benchmark fixtures —
  they double as manual smoke-test repos (copy to /tmp before indexing).
