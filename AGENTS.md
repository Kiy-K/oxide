# AGENTS.md

OXIDE: local incremental code index + hybrid retrieval (Rust, single crate).
Full docs: `README.md`; context-engineering rationale: `docs/context-engineering-notes.md`.

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
- Storage is SQLite behind the small `IndexBackend` trait (`src/index.rs`);
  DB lives at `<repo>/.oxide/index.db`.
- `fixtures/py_repo` and `fixtures/ts_repo` are committed benchmark fixtures —
  they double as manual smoke-test repos (copy to /tmp before indexing).
