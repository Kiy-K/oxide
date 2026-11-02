# TreeIndex / TreeRouter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an index-time repository tree (TreeIndex) with file- and directory-level evidence, and a
query-time router (TreeRouter) plus packer that select context before any shortlist exists. The
router is gated first by a routing-recall check (Phase 0).

**Architecture:**
- A new `src/tree/` module (`index.rs`, `router.rs`, `pack.rs`).
- Directory and file nodes, file postings and node vectors live in new SQLite tables. They are filled
  by a research-gated tree build that runs after `update_index`. The default index and context paths
  are untouched unless `OXIDE_TREE_INDEX=1` / `OXIDE_CONTEXT_STRATEGY=tree` is set.
- File BM25 documents are SQL aggregates of the existing per-symbol `lexical_postings`. A file card is
  embedded once per file; a directory's vector is the normalized mean of its children's.

**Tech Stack:** Rust (rusqlite, existing `lexical`, `embeddings`, `relations`); Python 3 + numpy for
the Phase 0 scorer; #40/#43 task-generation scripts.

**Spec:** `docs/superpowers/specs/2026-10-04-treeindex-treerouter-design.md`

## Global Constraints

- The default path stays byte-identical: `oxide context --json` with no tree env vars, `fixtures/benchmark.json`, and `tests/benchmark_gate.rs` are all unchanged.
- Frozen: RRF K=60, lexical 0.6 / semantic 0.4; symbol embeddings and `SYMBOL_TEXT_RECIPE` (`symbol-text:v1`); the `ContextPack` JSON shape; the CLI/MCP surface.
- Card recipe id `tree-card:v1`. Card = path + leading docstring/comment ≤ 400 chars + top-level `qualified_name: signature` lines in file order, whole card ≤ 2,000 chars, no body text.
- `meta.tree_index_version = "1"`, `meta.tree_card_recipe = "tree-card:v1"` and `meta.tree_vectors_fingerprint = <embedding fingerprint key>` are published together in one `set_meta_all` after a complete build. If any is absent or mismatched, the tree is unusable.
- Every tree write transaction calls `bump_generation`.
- The tree build requires `meta.lexical_index_version == LEXICAL_INDEX_VERSION`; otherwise it is skipped and the tree stays unusable.
- Packer constants: 350-token item cap, shrink-to-fit, 12-token overhead, 1-test cap, floor 0.15 × best value, lateral expansion 2 per anchor / 2 total from the first 5 packed, score × 0.4.
- Router constants: routing stops at cumulative file `est_tokens` ≥ 2 × budget; R2 escape F = 3; directory lexical = Σ child-file BM25 / √(n_files + 1).
- Builds and tests run as `cargo … -j 2`. Any step that runs an embedding model runs alone (one model process at a time), under `systemd-run --user --scope -p MemoryMax=4G -p MemorySwapMax=512M -p CPUQuota=200%`.
- Phase 0: develop only on the 149 #42 tasks, freeze one router, then validate on a **new** fresh set generated after the freeze (seed 44), not #43's tasks.

## Review Focus

1. **A file with no symbols or no postings** (binary, empty, unparsed). Expected: no file node, routing ignores it, no panic. *Test in Task 2.*
2. **A file deleted between index runs.** Expected: its node, postings and vector are removed, and its parent directory's counts and vector are recomputed. *Test in Task 1.*
3. **An index built before the tree tables existed, or with the tree env unset.** Expected: the tree is unusable, `context` falls back to the flat pipeline with diagnostic `tree: unavailable (not built)`, and the output is otherwise byte-identical. *Test in Task 7.*
4. **An embedder that is unavailable at query time.** Expected: lexical-only routing plus diagnostic `tree: lexical-only (no query vector)`. *Test in Task 4.*
5. **A repo whose files all sit in one directory, or at the root.** Expected: flattening routes files directly, and R1/R2 equal R0's file set. *Test in Task 4.*

---

### Task 1: Tree storage (schema + store methods)

**Files:**
- Modify: `src/storage/sqlite.rs` (append to `SCHEMA_SQL`; implement the new trait methods)
- Modify: `src/storage/backend.rs` (`IndexRead`/`IndexWrite` additions with empty default impls, so other implementors compile)
- Create: `src/tree/mod.rs` (constants + `TreeNode`)
- Modify: `src/lib.rs` (`pub mod tree;`)
- Test: `tests/tree_storage.rs`; extend `tests/index_generation.rs`

**Interfaces:**
- Produces, in `src/tree/mod.rs`:
  - `pub const TREE_INDEX_VERSION: &str = "1"`, `TREE_CARD_RECIPE = "tree-card:v1"`, and the keys `TREE_INDEX_KEY = "tree_index_version"`, `TREE_CARD_RECIPE_KEY = "tree_card_recipe"`, `TREE_FINGERPRINT_KEY = "tree_vectors_fingerprint"`;
  - `pub enum NodeKind { Dir, File }`;
  - `pub struct TreeNode { pub id: u64, pub kind: NodeKind, pub path: String, pub parent: Option<u64>, pub n_symbols: u32, pub est_tokens: u32, pub src_hash: u64, pub card_hash: u64 }`;
  - `pub fn node_id(kind: NodeKind, path: &str) -> u64`: `fnv1a64` of `"d:"`/`"f:"` + path.
- Produces, `IndexRead`:
  - `tree_nodes() -> Result<Vec<TreeNode>>`, ordered by path;
  - `tree_totals() -> Result<(usize, i64)>`, the file doc count and summed length;
  - `tree_postings(term) -> Result<Vec<(u64, u32, u32)>>`, as `(node_id, tf, doc_len)`;
  - `tree_vectors() -> Result<HashMap<u64, Vec<f32>>>`.
- Produces, `IndexWrite`:
  - `write_tree_files(rows: &[TreeNode], removed: &[String]) -> Result<()>`: upserts the file nodes, deletes the removed ones, and rebuilds their `tree_postings`/`tree_docs` as SQL aggregates of `lexical_postings` joined through `symbols.file`. One IMMEDIATE transaction, with a bump.
  - `put_tree_vectors(rows: &[(u64, Vec<f32>)]) -> Result<()>`
  - `clear_tree_vectors() -> Result<()>`
  - `write_tree_dirs(rows: &[TreeNode], vectors: &[(u64, Vec<f32>)]) -> Result<()>`: replaces all directory nodes and their vectors. Bumps.
- Tables:
  - `tree_nodes(id INTEGER PRIMARY KEY, kind TEXT CHECK(kind IN ('dir','file')), path TEXT NOT NULL UNIQUE, parent INTEGER, n_symbols INTEGER, est_tokens INTEGER, src_hash INTEGER, card_hash INTEGER)`
  - `tree_postings(term TEXT, node_id INTEGER REFERENCES tree_nodes(id) ON DELETE CASCADE, tf INTEGER)` with an index on `term`
  - `tree_docs(node_id INTEGER PRIMARY KEY REFERENCES tree_nodes(id) ON DELETE CASCADE, len INTEGER)`
  - `tree_vectors(node_id INTEGER PRIMARY KEY REFERENCES tree_nodes(id) ON DELETE CASCADE, dim INTEGER, vec BLOB)`
- Symbols are **logical children** of file nodes, joined by `symbols.file = tree_nodes.path`. No symbol rows go into `tree_nodes`.

- [ ] **Step 1: Write failing tests** in `tests/tree_storage.rs`:
  - `file_postings_are_the_sum_of_symbol_postings`: index the `fixtures/py_repo` copy, call `write_tree_files` for every file, then for one term assert that `tree_postings(term)`'s tf for `oxidepy/retry.py` equals the Σ tf of `lexical_postings(term)` over that file's symbols, and that its doc length equals the Σ of its symbols' doc lengths.
  - `removed_file_drops_node_postings_and_vector` (Review Focus 2).
  - Each of `write_tree_files`, `put_tree_vectors`, `clear_tree_vectors` and `write_tree_dirs` increases `generation()` by exactly 1. Add these to `tests/index_generation.rs`'s enumeration.
- [ ] **Step 2:** `cargo test -j 2 --test tree_storage` fails (methods are undefined).
- [ ] **Step 3:** Implement the schema, the trait methods and the SQL aggregation: `INSERT INTO tree_postings SELECT p.term, n.id, SUM(p.tf) FROM lexical_postings p JOIN symbols s ON s.id=p.symbol_id JOIN tree_nodes n ON n.path=s.file AND n.kind='file' WHERE s.file IN (…) GROUP BY p.term, n.id`; `tree_docs.len` = Σ `lexical_docs.len`.
- [ ] **Step 4:** `cargo test -j 2 --test tree_storage --test index_generation` passes, and `cargo test -j 2` passes overall (the default index only gains empty tables).
- [ ] **Step 5:** Commit: `feat: add TreeIndex storage tables and store methods (research)`.

### Task 2: TreeIndex build (cards, nodes, vectors, publish)

**Files:**
- Create: `src/tree/index.rs`
- Modify: `src/index/mod.rs` (`update_index_scoped`: after the embedding stage, `if overrides.tree_index { tree::index::update_tree(root, store, embedder)?; }`)
- Modify: `src/research.rs` (`ResearchOverrides.tree_index: bool` from `OXIDE_TREE_INDEX == "1"`)
- Test: `tests/tree_index_build.rs`

**Interfaces:**
- Consumes: Task 1's store methods and `TreeNode`.
- Produces:
  - `pub fn card_text(path: &str, symbols: &[Symbol], src: &str) -> String`
  - `pub fn update_tree(root: &Path, store: &mut dyn IndexWrite, embedder: &dyn EmbeddingProvider) -> Result<TreeReport>`
  - `pub struct TreeReport { pub files_written: usize, pub cards_embedded: usize, pub dirs: usize, pub published: bool }`
  - `pub fn tree_usable(store: &dyn IndexRead, embedder: &dyn EmbeddingProvider) -> Result<(), String>`; the `Err` carries the reason: `"not built"`, `"recipe mismatch"` or `"embedding space changed"`.

- [ ] **Step 1: Write failing tests:**
  - `card_text_is_deterministic_and_bounded`: the same input gives the same output; length ≤ 2,000; no body lines; starts with the path.
  - `full_build_publishes_meta_and_one_vector_per_file`: after `update_tree` on the py fixture, `tree_usable` is `Ok(())`, every file node has a vector, and every directory vector equals the L2-normalized mean of its children's (tolerance 1e-6).
  - `incremental_equals_full_rebuild`: modify one file, then compare `update_index` + `update_tree` with a clean full rebuild. All four tree tables must be equal. Same pattern as `tests/full_incremental_parity.rs`.
  - `file_without_symbols_has_no_node` (Review Focus 1).
  - `stale_lexical_version_skips_build`: when `lexical_index_version` ≠ current, `published == false` and `tree_usable` is `Err("not built")`.
  - `provider_switch_clears_and_reembeds_cards`: a different embedder fingerprint gives `Err("embedding space changed")` until `update_tree` reruns, then `Ok(())`.
- [ ] **Step 2:** The tests fail.
- [ ] **Step 3:** Implement.
  - Changed files = `files.content_hash` ≠ `tree_nodes.src_hash`; removed = tree file nodes with no `files` row.
  - `est_tokens` = Σ over the file's symbols of min(350, `estimate_tokens(body)`) + 12.
  - Cards are re-embedded where `card_hash` changed, or for all files on a fingerprint mismatch (`clear_tree_vectors` first), via `embedder.embed_documents`.
  - Directories are rebuilt bottom-up from the file nodes.
  - Publish the three meta keys with `set_meta_all` last.
- [ ] **Step 4:** The tests pass, and `cargo test -j 2` passes.
- [ ] **Step 5:** Commit: `feat: build TreeIndex (cards, nodes, vectors) behind OXIDE_TREE_INDEX`.

### Task 3: File-level lexical query

**Files:**
- Modify: `src/lexical.rs` (add `prepare_tree_from_store`; inline `#[cfg(test)]`)

**Interfaces:**
- Produces: `pub fn prepare_tree_from_store(store: &dyn IndexRead, query: &str) -> Result<LexicalQuery>`. It is the same as `prepare_from_store` but reads `tree_totals`/`tree_postings`. Score it with the existing `score(q, k1, b)`, using the k1/b constants the symbol channel uses.

- [ ] **Step 1:** Failing test `tree_query_ranks_the_file_with_the_term_first`: on the py fixture with the tree built, the query `"backoff retry"` gives `oxidepy/retry.py` the top file score.
- [ ] **Step 2:** It fails. **Step 3:** Implement, sharing `query_tokens` and without duplicating the scoring. **Step 4:** It passes.
- [ ] **Step 5:** Commit: `feat: file-level BM25 over TreeIndex postings`.

### Task 4: TreeRouter (R0 / R1 / R2)

**Files:**
- Create: `src/tree/router.rs` (inline unit tests); Test: `tests/tree_router.rs`

**Interfaces:**
- Consumes: `prepare_tree_from_store`, `tree_nodes`, `tree_vectors`.
- Produces:
  - `pub enum RouterKind { Flat, Beam { dirs: usize }, BeamEscape { dirs: usize, files: usize } }`
  - `pub struct RoutedFile { pub path: String, pub weight: f32 }`
  - `pub fn route(store: &dyn IndexRead, query: &str, qvec: Option<&[f32]>, kind: RouterKind, budget_tokens: usize) -> Result<(Vec<RoutedFile>, Vec<String>)>`; the second element holds diagnostics.
- Algorithm (the parts the signature doesn't fix):
  - Within each node kind, fuse the BM25 rank and the cosine rank: RRF with K=60, weights 0.6/0.4.
  - Directory lexical = Σ child-file BM25 / √(n_files + 1).
  - R1/R2 expand directories best-first, starting from the root's children. A directory with a single child is replaced by that child.
  - A directory is admitted while fewer than `dirs` directories are taken; files come from admitted directories in fused order.
  - R2 unions the global top-`files` files.
  - Stop once cumulative `est_tokens` ≥ 2 × budget.
  - Weight = the file's fused score / the max fused score.
  - Ties: path ascending.
  - With `qvec == None`, the semantic rank is dropped and the diagnostic `"tree: lexical-only (no query vector)"` is pushed.

- [ ] **Step 1: Write failing tests:**
  - `flat_router_orders_files_by_fused_score`.
  - `router_is_deterministic` (100 repeats, identical output).
  - `budget_stop_bounds_cumulative_est_tokens`: Σ `est_tokens` of the routed files minus the last one is < 2 × budget.
  - `single_directory_repo_routes_like_flat` (Review Focus 5).
  - `no_query_vector_routes_lexically_with_diagnostic` (Review Focus 4).
  - `escape_admits_global_top_file_outside_beam`: the best file sits in a low-scoring directory; R1 misses it, R2 includes it.
- [ ] **Step 2:** The tests fail. **Step 3:** Implement. **Step 4:** They pass, and `cargo test -j 2` passes.
- [ ] **Step 5:** Commit: `feat: TreeRouter with flat, beam and beam+escape routing`.

### Task 5: Phase 0 — routing recall on the dev set, then freeze

**Files:**
- Create: `examples/tree_build.rs` (`tree_build <root> <index.db>` runs `update_tree`)
- Create: `examples/tree_route_dump.rs` (`tree_route_dump <root> <index.db> <tasks.jsonl>`)
- Create: `docs/treeindex-eval/phase0/{build_trees.sh,score_routing.py,README.md}`

**Interfaces:**
- `tree_route_dump` writes one JSON line per task: `{id, flat_files: [...], routes: {"R0", "R1-d2", "R1-d3", "R1-d5", "R2-d2-f3", "R2-d3-f3", "R2-d5-f3": [paths]}}`. `flat_files` is the distinct-file order of `engine.search(limit 200, hybrid, expand false)`. Routes use budget 4096.
- `build_trees.sh` copies each dev task's `index.db` to `~/.cache/oxide-tree-eval/dev/<id>/index.db` (`cp --reflink=auto`, so #40's cache is never mutated) and runs `tree_build` on it. One process at a time, capped.

- [ ] **Step 1:** Fixture smoke test (in the example's `main`, or a test via `assert_cmd`): on the py fixture, every route key is present and `flat_files` is non-empty.
- [ ] **Step 2:** Run `build_trees.sh` over the 149 dev tasks, only while #43's chain is idle or paused (SIGSTOP). Expected: 149 published.
- [ ] **Step 3:** Run the harness. Then `score_routing.py` computes gold-file recall@{3,5,10} per config and for `flat_files`. Gold files are the keys of `g5_gold`. It reports the repo-clustered bootstrap Δ vs flat (10,000 resamples, `numpy.random.default_rng(0)`; reuse `cluster_idx` from `docs/treeselect-eval/scripts/score.py`).
- [ ] **Step 4: Freeze.**
  - Choose the config with the highest mean dev recall@5. Ties go to the simpler one: R0 < R1 < R2, then the smaller d.
  - Set `pub const FROZEN_ROUTER: RouterKind` in `src/tree/router.rs`, and record the table in `README.md`.
  - **If no config beats flat recall@5 on dev, stop:** record the negative result and skip Tasks 6–8.
- [ ] **Step 5:** Commit: `docs: TreeRouter Phase 0 dev results; freeze router`.

### Task 6: Phase 0 validation on a new fresh set

**Files:**
- Create: `docs/treeindex-eval/phase0/PROTOCOL.md`
  - Gate: frozen router recall@5 − flat recall@5, repo-clustered 95 % CI lower bound > 0.
  - Validity: ≥ 50 held-out and ≥ 50 ContextBench tasks, ≥ 6 held-out repos.
- Create: `docs/treeindex-eval/phase0/gen/` (copy of `docs/cap-removal-eval/scripts/{gen_all.sh,gen_heldout.sh,build_tasks.py}` with `J=~/.cache/oxide-tree-eval/fresh` and `build.SEED = 44`)

- [ ] **Step 1:** Commit #43's task lists (`~/.cache/oxide-capremoval-eval/tasks/{heldout,cb}-frozen.jsonl` → `docs/cap-removal-eval/tasks/`), so `excl.py` at the freeze commit excludes them.
- [ ] **Step 2:** Commit `PROTOCOL.md` and the generation scripts before generating anything (this is the freeze).
- [ ] **Step 3:** Run `gen_all.sh <freeze-commit>`, then `build_trees.sh` and the harness on the fresh tasks, then `score_routing.py --frozen`. Expected: one gate line, PASS or FAIL.
- [ ] **Step 4:** Commit the results and the README verdict. **If it FAILs, stop** and skip Tasks 7–8.

### Task 7: TreePacker + context integration with fallback

**Files:**
- Create: `src/tree/pack.rs`
- Modify: `src/context.rs` (one branch at the top of `build_context_with`)
- Modify: `src/research.rs` (`context_strategy: Option<RouterKind>`; `OXIDE_CONTEXT_STRATEGY=tree` selects `FROZEN_ROUTER`)
- Test: `tests/tree_context.rs`

**Interfaces:**
- Consumes: `route`, `FROZEN_ROUTER`, `tree_usable`, `RelationGraph::neighbors`, `render_snippet`, `Role::assign`.
- Produces: `pub(crate) fn pack(root: &Path, engine: &RetrievalEngine<'_>, task: &str, routed: &[RoutedFile], opts: &ContextOptions, diagnostics: Vec<String>) -> Result<ContextPack>`.
- Algorithm:
  - Candidates are every symbol of the routed files, scored by symbol BM25 (postings filtered to those ids) and symbol cosine (vectors by id), fused with RRF 0.6/0.4.
  - Greedy by value/token, where value = `w_f` × fused × 1/√(k+1).
  - Stop when nothing fits or value < 0.15 × the best value.
  - Then lateral expansion: 2 per anchor, 2 in total, from the first 5 packed, score × 0.4.
  - Output in production role order. Reasons gain `tree-route←<file>`.

- [ ] **Step 1: Write failing tests:**
  - `default_strategy_is_byte_identical`: `oxide context --json` with the env unset equals the pre-change binary on the py and ts fixtures.
  - `tree_strategy_falls_back_when_unusable` (Review Focus 3): the output equals the flat pipeline plus the diagnostic `"tree: unavailable (not built)"`.
  - `pack_respects_budget_and_test_cap`.
  - `pack_is_deterministic`.
  - `lateral_items_come_after_routed_items`.
- [ ] **Step 2:** The tests fail. **Step 3:** Implement. **Step 4:** They pass. Then `cargo fmt`, `cargo clippy -j 2 --all-targets` clean, `cargo test -j 2` green, `./target/release/oxide eval --config fixtures/benchmark.json` unchanged.
- [ ] **Step 5:** Commit: `feat: TreePacker and tree context strategy (research, default off)`.

### Task 8: Phase 1 preregistration (end to end)

- [ ] **Step 1:** Open a GitHub issue.
  - Arms: `TR` (TreeRouter + TreePacker, token-matched to A) vs A and vs B.
  - Gates (#42's metrics, repo-clustered bootstrap): coverage CI lower bound > 0 vs A **and** vs B; rel/used lower bound ≥ −5 %; per-stratum point Δ ≥ 0; latency/RSS ≤ 5 %; P1 byte parity.
  - Data: a new fresh set (seed 45), generated after the freeze.
- [ ] **Step 2:** Copy the issue body verbatim into `docs/treeindex-eval/phase1/PROTOCOL.md` with its sha256, and commit before generating any Phase 1 task. Running it is a separate request.
