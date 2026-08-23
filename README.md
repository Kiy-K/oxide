# OXIDE

Fast local, incremental, structure-aware code index and retrieval engine.
Given a repository, OXIDE indexes code at the **symbol level** (Tree-sitter)
and returns the smallest useful set of relevant symbols — for search, review
context, debugging, and downstream coding agents.

Not an LLM wrapper. The product is the code intelligence and retrieval layer.
Works fully offline; the default embedder is deterministic and needs no model.

## Install

```bash
cargo build --release          # binary at target/release/oxide
```

Requires Rust 1.75+ and a `git` binary on PATH (used only for `review`).

## Usage

```bash
oxide index .                       # index or incrementally update a repo
oxide search RetryPolicy            # hybrid lexical+semantic+structural
oxide search "authentication token refresh" --limit 8
oxide search RetryPolicy --mode lexical   # exact identifier, no embeddings involved
oxide search "where failed requests are retried" --mode semantic
oxide review --diff HEAD~1          # review context from a git diff
oxide stats
oxide eval --config fixtures/benchmark.json   # committed benchmark
```

Search output per hit: path, symbol, kind, line range, score, retrieval
reason (`lexical=…; semantic=…; uses←X; test←Y`), and a source snippet.
`--json` emits machine-readable output for agent integration.

## What gets indexed

- **Python**: modules, classes, functions, methods (decorator spans included), imports
- **TypeScript/TSX**: functions, classes, methods, interfaces, type aliases,
  enums, exported declarations (incl. arrow-function consts), imports
- Discovery respects `.gitignore`, skips `.git`, build/cache/vendor dirs
  (`node_modules`, `target`, `dist`, `.next`, `__pycache__`, `.venv`, …),
  binaries (NUL sniff), lockfiles, and generated artifacts (`*.min.js`,
  `*.d.ts`, `-gen.py`, …)

Each symbol stores file, language, kind, line span, content hash, signature,
imports, references, parent — enough to reconstruct context later.

## Architecture

```text
src/
├── scanner      repo discovery & filtering (ignore crate + denylists)
├── parser       tree-sitter plumbing + LanguageExtractor trait
├── languages    python.rs, typescript.rs (TS + TSX grammars)
├── symbols      core model, stable FNV-1a hashing
├── index        SQLite storage behind IndexBackend trait + incremental pipeline
├── embeddings   EmbeddingProvider trait + offline hashed embedder (256-dim)
├── retrieval    BM25 lexical + cosine semantic fused via RRF + structural expansion
├── gitutil      unified-diff parsing (git CLI)
├── review       diff → changed symbols → related context pack
├── eval         committed benchmark harness
└── cli          clap-based CLI
```

### Incrementality

Two hash levels keep reindexing proportional to change:

1. **File hash** — unchanged files are never reparsed.
2. **Symbol content hash** — within changed files, only modified symbols are
   re-embedded; stable symbol ids preserve unchanged embeddings across edits.

Deletions purge symbols and stale embeddings in the same pass. Measured on
the Python fixture (43 symbols): initial index 9 ms, no-change reindex 2 ms,
two-symbol edit rewrote exactly 2 embeddings and reused 41, DB size ~120 KB.

### Hybrid retrieval

- **Lexical**: BM25 over names/signatures/paths/references (works with zero embeddings)
- **Semantic**: provider-based vectors; default is a deterministic hashed
  bag-of-tokens embedder (swap in any model by implementing one trait)
- **Structural expansion**: strong hits pull in parents, children, referenced
  definitions, imported definitions, and related tests (`test_*`,
  `*_test.py`, `*.spec.ts(x)`) as *additional* context that never displaces
  direct matches

Every hit lists why it was selected. Import resolution probes relative paths
and extension/index conventions against indexed files; unresolvable imports
are dropped rather than guessed.

## Benchmark

Committed fixtures (`fixtures/py_repo`, `fixtures/ts_repo`) plus ground truth
in `fixtures/benchmark.json`. Run:

```bash
oxide eval --config fixtures/benchmark.json
```

Latest results (Recall@5, K=5):

| mode        | mean recall@5 | mean precision@5 | avg ctx bytes |
|-------------|---------------|------------------|---------------|
| vector-only | 0.818         | 0.182            | ~1770 B       |
| hybrid      | **0.909**     | **0.200**        | ~2160 B       |

Hybrid wins on aggregate evidence, driven by structural expansion (e.g. it is
the only mode retrieving the TSX related-test). Semantic tasks still miss one
of two targets each — see limitations. A CI gate
(`tests/benchmark_gate.rs`) fails if a ranking change drops hybrid below
vector-only.

## Tests

```bash
cargo test            # unit + integration (incremental, review e2e, benchmark gate)
cargo fmt --check && cargo clippy --all-targets
```

## Limitations

- Default embeddings are hashed tokens: real semantic similarity is shallow;
  bring a real embedding model via `EmbeddingProvider` for stronger semantics.
- Reference extraction is identifier-name intersection, not scope analysis;
  high-confidence relations only (same-name definitions, resolvable imports).
- TS bare imports (`import x from 'pkg'`) resolve only if `pkg` matches an
  indexed path — node_modules is intentionally not consulted.
- Module-symbol hashes ignore body edits (imports + first line), so large
  body-only changes may leave a module's embedding slightly stale until its
  header changes.
- Review produces context packs, not LLM verdicts, by design.

## v0.1 non-goals

No web frontend, cloud service, graph database, auth, plugins, or agentic
editing. Local files in, ranked symbols out.
