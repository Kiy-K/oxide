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

Semantic search runs fully local via llama.cpp: `scripts/embedder.sh start`
(starts an OpenAI-compatible embeddings endpoint, ~0.3 GB RSS), then
`export OXIDE_EMBED_URL=http://127.0.0.1:8191/v1/embeddings OXIDE_EMBED_MODEL=qwen3-Q8_0`.
Without it OXIDE uses the offline hashed embedder.

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

Deletions purge symbols and stale embeddings in the same pass.

### Performance

Measured by `scripts/perf.sh` (release build, deterministic synthetic repo,
this machine, warm OS cache — treat as relative indicators, not absolutes):

| repo size              | cold index | no-change reindex | single-symbol edit | hybrid search |
|------------------------|-----------:|------------------:|-------------------:|--------------:|
| 804 files / 3,211 sym  | ~300 ms    | ~32 ms            | ~48 ms             | ~40 ms        |
| 2,404 files / 9,611 sym| ~880 ms    | ~106 ms           | ~121 ms            | ~120 ms       |

A one-symbol edit rewrites exactly its own embedding (3,209/3,211 reused at
the small scale). Optimizations that produced these numbers:

- single read per changed file (hash + parse + references share the buffer)
- batched embedding loads (one SQL query, not N) with lazy per-engine cache
- allocation-light tokenizer feeding a lexicon built once per engine
- bounded parallel parse/embed pool (`min(cpus, 4)` threads — laptop-friendly)
- `codegen-units = 1`; optional further gain via
  `RUSTFLAGS="-C target-cpu=native" cargo build --release`

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

## Benchmarks

### Official: ContextBench gold contexts (external tasks)

`scripts/agent_eval/contextbench_run.py` samples issue-resolution tasks from
[ContextBench](https://arxiv.org/abs/2602.05892) (Apache-2.0), indexes each
repository at its base commit, retrieves context for the real issue text, and
scores against human-annotated gold contexts using ContextBench's own metric
code. Interim results (20 tasks, Python+TypeScript, small-repo subset):

| condition | file R/P/F1          | line R/P/F1     | tokens |
|-----------|----------------------|-----------------|-------:|
| lexical   | .637/.204/.309       | .583/.027/.052  | 3105   |
| vec-only  | .479/.203/.285       | .266/**.104**/**.150** | **1545** |
| hybrid    | .679/**.239**/**.354** | .578/.035/.066 | 2837   |
| budgeted  | **.767**/.141/.238   | .500/.047/.087  | 4087   |

Honest reading: hybrid has the best balanced file-level F1; the budgeted pack
trades precision for widest file coverage; pure vectors are the most
token-efficient. Same-agent tier (headless opencode, 4 tasks x 4 conditions): no condition
beats stock on gold-file utilization at this sample size; hybrid is fastest
(290s vs 348s stock) with fewest unnecessary edits. Full methodology:
`docs/context-engineering-notes.md`.

### Committed regression fixture (`fixtures/benchmark.json`)

```bash
oxide eval --config fixtures/benchmark.json
```

| mode        | mean recall@5 | mean precision@5 |
|-------------|---------------|------------------|
| vector-only | 0.818         | 0.182            |
| hybrid      | **0.909**     | **0.200**        |

A CI gate (`tests/benchmark_gate.rs`) fails if a ranking change drops hybrid
below vector-only.

## Tests

```bash
cargo test            # unit + integration (incremental, review e2e, benchmark gate)
cargo fmt --check && cargo clippy --all-targets
```

## Using OXIDE from a coding agent

OXIDE is designed as a context supplier for agents. Stable interfaces:

- **Symbol identity**: `path#QualifiedName` (e.g. `src/net/retry.ts#ExponentialBackoff`)
  — the same key used in benchmark ground truth and review output.
- `oxide search "query" --json` → array of hits with flattened symbol metadata
  (`file`, `qualified_name`, `kind`, `start_line`, `end_line`, …), `score`,
  `reasons[]` (machine-readable evidence), and `snippet`.
- `oxide review --diff HEAD~1 --json` → `{ range, changed_files[],
  changed_symbols[], related[] }`; feed it straight into a model prompt or a
  human reviewer.
- Keep a process warm and call retrieval repeatedly: the lexical index and
  vector cache are built once per engine instance, so follow-up queries skip
  index construction.

Guiding principle: fewer symbols with stronger evidence beats more code.

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
