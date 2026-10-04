# TreeIndex / TreeRouter — design

Date: 2026-10-04. Status: design approved; spec under review. Production is unchanged until the gates in §7 pass.

## 1. Intent

OXIDE's context loss sits at the **selection seam**: which already-generated candidates reach the
roughly 7 pack slots. This seam holds 84 % of natural lost weight (`docs/retrieval-failure-taxonomy/`
§13; oracle D +0.22, B16 +0.15).

- Request-time features over the flat per-symbol candidate list carry no transferable signal (#32).
- Re-ordering that list with a tree built after retrieval has no leverage (#42, scoped in
  `docs/treeselect-eval/README.md`).
- The taxonomy's §14 conclusion is that a *different representation of candidates* is needed.
  Symbol embeddings are identifier-only (`symbol_embed_text`), and there is no file- or
  directory-level evidence at all.

**Goal:** build a first-class repository tree at **index time** (TreeIndex) with its own lexical and
semantic evidence per node, and **route through it at query time before any shortlist exists**
(TreeRouter). The router replaces today's selection (16 seeds, caps) and allocation. Graph
relations become bounded lateral expansion only. This is PageIndex's "decide where to look first"
idea, deterministic and local, with no LLM in the loop.

**Success:** at the same tokens, TreeRouter packs deliver more gold than the shipped selector (A)
**and** than the capless flat selector (B, #43). Each is checked by its own preregistered gate.

## 2. Constraints

- Local-first, deterministic Rust. No new model, no LLM. The one extra embedding per file uses the
  existing provider and cache.
- The default path stays byte-identical until promotion. The strategy is selected by a research
  override (`OXIDE_CONTEXT_STRATEGY=tree`), resolved in `ResearchOverrides`.
- Frozen: retrieval channels, RRF (K=60, 0.6/0.4), symbol embeddings and `SYMBOL_TEXT_RECIPE`, the
  `ContextPack` JSON shape, and the CLI/MCP surface.
- All index invariants in `docs/agents/invariants.md` hold: the generation is bumped in every write
  transaction, versioned artifacts are published only after a complete build, and embedding recipes
  are fingerprinted.
- Laptop limits: one model process at a time, CPU and RAM capped (`local-model-limits` memory).

## 3. Architecture

New module `src/tree/`, three units with one job each:

| unit | job | depends on |
|---|---|---|
| `tree/index.rs` (TreeIndex) | build and incrementally update the node hierarchy and its evidence | `IndexRead`/`IndexWrite`, `lexical` tokenizer and weights, `EmbeddingProvider` |
| `tree/router.rs` (TreeRouter) | score nodes for a query and return the routed files with weights | TreeIndex reads, query terms and vector |
| `tree/pack.rs` (TreePacker) | choose symbols inside routed files under the token budget, then add lateral graph items | symbol channels restricted to routed files, `RelationGraph`, `render_snippet` |

`context.rs` gains one branch: when the strategy is `tree` **and** the TreeIndex is usable, call
router → packer and return the `ContextPack`. Otherwise run the existing pipeline unchanged.

### 3.1 Storage

New tables in `index.db`:

- `tree_nodes(id INTEGER PRIMARY KEY, kind TEXT CHECK(kind IN ('dir','file')), path TEXT UNIQUE,
  parent INTEGER, n_symbols INTEGER, est_tokens INTEGER)`. The root is the directory `""`. A file's
  `est_tokens` is the sum of its symbols' 350-capped snippet estimates.
- **Symbols are logical children of file nodes.** `tree_nodes` persists only directory and file
  nodes. The symbol level of `repo → directory/module → file → symbol` is the existing `symbols`
  table, joined to its file node by path (`symbols.file = tree_nodes.path`). It is not duplicated in
  `tree_nodes`. Symbol-level evidence stays in `lexical_postings` and the symbol embeddings.
- `tree_postings(term TEXT, node_id INTEGER, tf INTEGER, len INTEGER)`: **file** documents only.
  Each is the union of the file's symbol documents under the same tokenizer and name/body weights as
  `lexical_postings`.
- `tree_vectors(node_id INTEGER PRIMARY KEY, dim INTEGER, vec BLOB)`:
  - a file's vector is the embedding of its **file card**;
  - a directory's vector is the L2-normalized mean of its children's vectors. No model call is needed.
- **File card** (recipe `tree-card:v1`): the path, the module docstring or first comment (≤ 400
  characters), then the file's top-level symbol `qualified_name: signature` lines in file order,
  truncated to 2,000 characters. Deterministic. Body text stays out, as it does for symbol embeddings.
- `meta.tree_index_version` and `meta.tree_card_recipe` are published together at the end of a
  complete build. If either is absent or different, the TreeIndex is unusable and readers fall back,
  following the `lexical_index_version` contract.

### 3.2 Index-time flow

- **Full build** (end of `update_base`, after symbols and lexical):
  1. Derive directory and file nodes from `symbols.file` paths.
  2. Write the file postings and `est_tokens`.
  3. Embed the cards through `embed_documents`, which is cached by content hash.
  4. Compute directory vectors bottom-up.
  5. Publish the meta keys in one `set_meta_all` transaction.
- **Incremental:** a changed file's node, postings and card vector are rewritten inside its
  `replace_file` transaction. Removed files drop their node. Affected ancestor directories have their
  vectors and counts recomputed in one write transaction. Every write bumps `index_generation`
  (`tests/index_generation.rs` enumerates the paths).
- **Embedding-space change:** a provider or recipe mismatch clears `tree_vectors` together with the
  symbol vectors, through the existing `EmbeddingSpace::plan_write` path, and re-embeds the cards.

## 4. Query-time flow

1. **Inputs.** The query terms and query vector, computed once and shared with the symbol channels.
2. **Node scores.**
   - **File:** BM25 over `tree_postings` (the shared `lexical::score`, k1/IDF as for symbols, over
     the file-document collection), and the cosine against file vectors (an exact scan; files are far
     fewer than symbols).
   - **Directory:** its own vector cosine, plus lexical = Σ child-file BM25 / √(n_files + 1).
   - Lexical and cosine ranks are fused with the frozen RRF (K=60, 0.6/0.4) within each node kind.
3. **Routing.** Phase 0 (§7) chooses one of three frozen candidate routers by routing recall:
   - **R0 flat:** rank all files by their fused score.
   - **R1 beam:** keep the top-D directories by fused score (single-child directories are flattened),
     then rank their files.
   - **R2 beam + escape:** R1 ∪ the global top-F files (F = 3), so a strong file in an unrouted
     directory survives.
   - The router, D and F are chosen on the development set and frozen before validation (§7).
   - Routing stops when the routed files' cumulative `est_tokens` reaches 2 × budget. Each routed file
     carries a weight `w_f` (its fused score, normalized).
4. **Symbols inside routed files.** Symbol BM25 (postings filtered to the routed files' ids) and
   symbol cosine (vectors fetched by id), fused as today. The `__module__` symbol counts as a file's
   fallback item.
5. **Packing (replaces the caps).** Greedy by marginal value per token:
   - value = `w_f` × symbol fused score × 1/√(k + 1), where k is the number of items already packed
     from that file (diminishing returns per file);
   - cost = rendered tokens: `render_snippet`, 350-token cap, shrink-to-fit, 12-token overhead;
   - stop when nothing fits or value < 0.15 × the best value (the existing floor fraction);
   - the 1-test cap is kept; roles come from `Role::assign`, and the output is in role order. #42
     showed that role-agnostic order promotes tests and halves primaries' windows.
6. **Lateral expansion.** `RelationGraph::neighbors` of the first 5 packed symbols (2 per anchor,
   2 in total, score × 0.4), offered while budget remains.
7. **Output.** The same `ContextPack`. Reasons render as `tree-route←<file>` plus the existing
   channel reasons.

## 5. Errors and fallback

A request never fails because of the tree:

- TreeIndex unusable (absent or mismatched version or recipe), or zero routed files → the flat
  pipeline, with `diagnostics: ["tree: unavailable (<reason>)"]`.
- Embedder unavailable → lexical-only routing (cosine terms dropped), with a diagnostic.
- A file read failure during rendering → the existing `render_snippet` behavior (empty snippet, item
  skipped).

## 6. Testing

- **Unit:**
  - card text is deterministic;
  - file postings equal the union of the file's symbol postings;
  - directory vectors are the normalized mean of their children;
  - the router is deterministic, with ties broken by path, then id;
  - packer budget and test-cap invariants hold.
- **Index:**
  - an incremental update equals a clean full rebuild for all tree tables (pattern of
    `tests/full_incremental_parity.rs`);
  - every tree write bumps the generation (`tests/index_generation.rs`);
  - a version or recipe mismatch makes the tree unusable and the pack falls back;
  - a provider switch clears and re-embeds the cards.
- **Default path:**
  - `oxide context --json` is byte-identical with the strategy unset;
  - `fixtures/benchmark.json` is unchanged;
  - `tests/benchmark_gate.rs` passes.

## 7. Evaluation gates

- **Phase 0 (routing recall, before the router and packer are finished).**
  - Metric: gold-file recall@{3, 5, 10} for R0, R1 and R2, vs the distinct-file order of the flat
    fused list (today's candidate generation).
  - Development: the 149 #42 tasks only. R0, R1 and R2 (and their D/F constants) are compared there,
    and one router is chosen and **frozen**.
  - Validation: a **new fresh task set**, generated after the freeze by #40's procedure with a new
    seed and with exclusions covering every committed task. It is **not** #43's tasks, which will
    already have been inspected.
  - Proceed only if the frozen router beats flat recall@5 on that fresh set: repo-clustered paired
    bootstrap, CI lower bound > 0. Otherwise stop and record the result.
- **Phase 1 (end to end, preregistered in its own issue before any scored run).**
  - Arm `TR` = TreeRouter + TreePacker, token-matched to A.
  - Gates vs A **and** vs B: gold-line coverage CI lower bound > 0, rel/used lower bound ≥ −5 %,
    non-negative point Δ per stratum, latency/RSS ≤ 5 %, default-path byte parity.
  - #42's metrics and the repo-clustered bootstrap.
  - Data: fresh tasks never used for development or Phase 0 validation, so a separate new set is
    generated after Phase 1's preregistration.
- **Promotion** to the default strategy is a separate decision after Phase 1 passes.

## 8. Out of scope

- LLM or model-generated node summaries: a later plug-in for node evidence, not now.
- Jev or any remote evidence.
- Changes to RRF, symbol embeddings or `SYMBOL_TEXT_RECIPE`.
- Any CLI/MCP flag or JSON change. The strategy switch is research-only until promotion.
- Further shortlist-reranking experiments (#42 closed that line).
