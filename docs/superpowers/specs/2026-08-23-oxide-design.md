# OXIDE v0.1 — Design

Requirements live in the project brief (see conversation / README). This doc records
resolved implementation choices for autonomous execution.

## Resolved choices

1. **Crate layout**: single crate `oxide` (bin + lib). Modules: `cli`, `scanner`,
   `languages::{python,typescript}`, `parser`, `symbols`, `index`, `embeddings`,
   `retrieval`, `gitutil`, `review`, `eval`. A workspace adds compile friction without
   adding a boundary we need yet; module visibility enforces seams.
2. **Parsing**: Tree-sitter via `tree-sitter`, `tree-sitter-python`, and
   `tree-sitter-typescript` (covers both TS and TSX grammars). A `LanguageExtractor`
   trait per language keeps grammar addition additive.
3. **Storage**: SQLite via `rusqlite` (bundled) behind a small `IndexStore` trait.
   Tables: files, symbols, embeddings (BLOB), meta. Replaceable later.
4. **Lexical search**: BM25 over symbol names/signatures/docstrings + file path tokens,
   built into memory from the symbols table at query time. Persisted token data is not
   needed separately; rebuild cost is one table scan (v0.1 scale).
5. **Embeddings default provider**: deterministic local hashed bag-of-tokens embedder
   (hashing trick, L2-normalized, no network, no model download). Provider trait
   (`EmbeddingProvider`) allows swapping ONNX/API providers. OXIDE stays fully useful
   with no model: lexical + structural retrieval work standalone.
6. **Git integration**: shell out to the `git` binary (`diff --unified=0`, `ls-files`);
   parse unified diffs in Rust. Avoids heavyweight git libs for v0.1 needs.
7. **Walking/filtering**: `ignore` crate (respects `.gitignore`, skips `.git`),
   plus explicit deny-list for build/cache/vendor dirs, binary detection by extension
   and NUL sniffing, generated-file heuristics (`*.min.js`, lockfiles).
8. **Incrementality**: two hash levels — per-file content hash (skip reparse entirely)
   and per-symbol content hash (skip re-embedding of unchanged symbols). Deleted
   files/symbols are purged in the same transaction as upserts.
9. **Relationships** (high-confidence only): enclosing parent, imports (raw module
   paths), same-file reference→definition resolution, import-resolved cross-file
   references when unambiguous, name-based related-test matching
   (`test_*`/`*_test.py`/`*.spec.ts`/`*.test.ts*`). Stored as plain tables/JSON columns.
10. **Hybrid ranking**: `score = w_l·lexical + w_s·semantic (+ structural boosts)`.
    Weights fixed in v0.1, exposed as CLI flags for eval. Every result carries a
    machine-readable reason string listing contributing evidence.
11. **Eval**: committed fixtures `fixtures/py_repo`, `fixtures/ts_repo` +
    `fixtures/benchmark.json` ground truth. `oxide eval` runs queries in both modes,
    prints Recall@K / Precision@K / returned-symbol counts / context bytes; also
    runnable as an integration test so regressions fail CI.

## Non-goals

As specified: no web/cloud/graph-db/auth/plugins/model-in-the-loop.

## Risks

- Hashed embeddings are weak semantics; acceptable because hybrid must measurably beat
  vector-only on the benchmark or we say so honestly.
- Tree-sitter TS extraction edge cases (decorators, overloads): extract conservatively,
  prefer missed symbol over wrong span.
