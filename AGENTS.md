# AGENTS.md

OXIDE: local incremental code index + hybrid retrieval (Rust, single crate).
Full docs: `README.md`; context-engineering rationale: `docs/context-engineering-notes.md`.

**Reviewing a change (Codex, Claude, or human)?** Read `docs/review/README.md`
first — it encodes OXIDE-specific invariants, severities, and evidence
standards so review comments catch real regressions instead of generic
style notes. The "load-bearing invariants" below are BLOCKER-severity under
that policy.

## Commands

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
  Never change id composition casually.
- All ids/hashes cross SQLite as `as i64` bit-casts (u64 → i64 → u64). The
  casts look like bugs; they are not.
- Duplicate qualified names per file are deduped (first wins) in
  `parser.rs::parse_file`. Real repos have overloads/conditional defs; without
  this, indexing dies on `UNIQUE constraint failed: symbols.id`.
- Retrieval expansion must never displace direct hits: direct results keep
  their pre-expansion scores; expansion-only items are appended after. The
  benchmark gate depends on this invariant (`src/retrieval.rs`).
- `RetrievalEngine::search` runs lexical (BM25) and semantic (embed_query +
  dot-product scan) concurrently via plain `std::thread::scope` — not a tokio
  task. This is deliberate: `oxide context`/`oxide search` from the CLI run
  fully synchronously with no tokio runtime at all (`cli.rs::run_mcp`'s own
  comment says so), while MCP already runs the whole service call inside
  `spawn_blocking`. `std::thread::scope` is the one primitive that works
  identically from both without adding a Cargo.toml tokio feature. The
  closures inside the scope capture narrow field references
  (`&self.lexical`, `&self.symbols`, `self.embedder`), never `self` — `self:
  &RetrievalEngine` is not `Send` (it holds `store: &dyn IndexBackend` and
  `vectors: RefCell<..>`, neither `Sync`) even though the closures never
  touch those fields; capturing `self` wholesale fails to compile for a
  reason that has nothing to do with what the closure actually reads.
- `RetrievalMode` (`Fast`/`Balanced`/`Quality`, `retrieval.rs`) only gates
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
- An embedding's cache-invalidation key must always equal (a hash of)
  `index::embed_text(symbol)` exactly — never a proxy for it. The module
  symbol's `content_hash` is intentionally coarse at parse time (imports +
  first line, `parser.rs`), but `update_index` overwrites it once `references`
  are resolved (`content_hash(&embed_text(s))`) — references are part of
  `embed_text` but aren't known until after parsing. This override is scoped
  to files that used the coarse formula (`used_coarse_module_hash` in
  `update_index`); the empty-file fallback module symbol already hashes full
  source and must keep doing so, or comment-only files silently stop
  reporting edits (`tests/embedding_staleness.rs` pins both).
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

## Embeddings / providers

- Provider selection: explicit `--embedder URL` > `$OXIDE_EMBED_URL` > offline
  `HashedEmbedder` (no server needed; benchmark gate runs on this).
- Provider identity = `http:{model}@{endpoint}` where model comes from
  `$OXIDE_EMBED_MODEL`. Switching the served GGUF quant WITHOUT changing that
  label silently keeps stale, incomparable vectors. Index meta detects the
  change and wipes embeddings — but only if the label differs.
- HTTP failures return empty vectors by design; the indexer skips and counts
  them (`embed_failures`), retrieval ignores length-mismatched ones.
- Start/stop the local llama.cpp server with `scripts/embedder.sh start|stop`
  (~0.3 GB RSS with the capped profile; Q4_K_M third-party quants are broken,
  stick to official Q8_0).

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
  Language support = implement `LanguageExtractor` + register in
  `src/parser.rs` (currently python, typescript/tsx).
- `parser.rs::extractor_for()` (the default) routes through
  `src/languages/tags.rs`'s generic `TagsExtractor`: a `LanguageProfile`
  (grammar + `queries/*_tags.scm` + `queries/*_locals.scm`) feeds the official
  `tree-sitter-tags` crate, and OXIDE reconstructs parent/containment and
  Python's method-vs-function split itself via byte-range nesting over the
  flat tag list (tags carry no parent info at all). Adding a language is
  meant to mostly be grammar + `.scm` + normalization tests, not a new
  procedural extractor. The original handwritten, per-language AST-walking
  extractors (`languages/python.rs`, `languages/typescript.rs`) are retained,
  reachable via `extractor_for_handwritten()`, not deleted: they cover a
  narrow but real gap upstream tags.scm doesn't — decorator-inclusive spans
  (`@app.route`, `@Injectable()`) — documented and pinned by
  `languages::tags::tests::decorator_line_is_not_included_in_span`. Adopting
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
  (`retrieval.rs`, two `OnceCell`-lazy reverse indexes over `Symbol.calls`/
  `bases` — `RelationGraph::build()` itself does zero extra work whether or
  not those fields are populated). Any caller of `callers_of`/
  `implementors_of` MUST intersect the result with an explicit bounded file
  scope before it reaches context output — the lookup itself is repo-wide
  by construction and a 902-file synthetic-repo measurement showed 60x more
  results unfiltered than the same lookup scoped to a realistic seed pool;
  `context.rs` enforces this the same way it always has, with a
  `scope_files` filter built from the seed pool's own files (capped),
  applied to the `callers_of` result before use — see
  `docs/review/structural-and-language.md`'s LANG-001. Attribution (mapping
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
  DB lives at `<repo>/.oxide/index.db`.
- `fixtures/py_repo` and `fixtures/ts_repo` are committed benchmark fixtures —
  they double as manual smoke-test repos (copy to /tmp before indexing).
