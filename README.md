# OXIDE

A **Context Engine for coding agents**, powered by **Selective Code
Indexing**: given a repository, OXIDE indexes code at the symbol level and
returns the smallest useful, bounded working set of relevant code for a
task — not every byte reachable from the project root.

OXIDE is not a vector database, not a code graph, not a Tree-sitter indexer,
and not a RAG framework — those are layered implementation components
underneath it (see Architecture below), not what it is. It is not an LLM
wrapper either; the product is the context-supply layer a coding agent
calls before it starts reading and editing. Works fully offline; the
default embedder is deterministic and needs no model.

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
oxide status --json
oxide index . --json
oxide search "where is authentication handled?" --json
oxide context --task "fix refresh token validation" --budget-tokens 4096 --json

oxide index .                       # index or incrementally update a repo
oxide status .                      # show index freshness and counts
oxide search RetryPolicy            # hybrid lexical+semantic+structural
oxide search RetryPolicy --mode lexical   # exact identifier, no embeddings involved
oxide review --diff HEAD~1          # review context from a git diff
oxide stats
oxide eval --config fixtures/benchmark.json   # committed benchmark
```

Agent-facing commands use `--json` and write only the result to stdout. Runtime
failures return a JSON object with `error.code`, `error.action`, and
`error.message`, exit 1, and leave human diagnostics on stderr. `action` is
one of `index`, `repair`, `retry`, `fall_back`, `stop` — what to do next
without parsing `message`. Malformed command-line invocations are
handled by Clap with exit 2. Read commands require an existing index; run
`oxide index PATH --json` first.

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

Conceptually, a request flows through one pipeline regardless of which
transport (CLI today; MCP in a future phase) initiates it:

```text
Repository
   |
Selective Code Indexing
   |-- syntax evidence      (Tree-sitter: symbols, spans, signatures)
   |-- lexical evidence     (BM25 over names/signatures/paths/bodies)
   |-- semantic evidence    (embedding provider; offline hashed by default)
   `-- structural evidence  (parent/child, imports, references, tests)
       |
Evidence retrieval
       |
ranking / canonicalization   (RRF fusion, deterministic tie-break)
       |
context allocation           (token-budgeted pack, direct hits kept, omitted[] explains cuts)
       |
bounded coding working set
       |
coding agent
```

Tree-sitter, the embedding provider, SQLite storage, and the RRF fusion
math are implementation details of "Selective Code Indexing" and "ranking /
canonicalization" above — swappable, and not the thing an agent needs to
know about to use OXIDE (see `docs/agent-usage-policy.md`). The module
layout below maps onto that pipeline directly:

```text
src/
├── scanner      repo discovery & filtering (ignore crate + denylists)
├── parser       tree-sitter plumbing + LanguageExtractor trait
├── languages    python.rs, typescript.rs (TS + TSX grammars)
├── symbols      core model, stable FNV-1a hashing
├── index        SQLite storage + incremental indexing pipeline
├── embeddings   provider abstraction + offline hashed embedder
├── service      stable repository/application boundary for CLI and future MCP
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
this machine, warm OS cache — treat as relative indicators, not absolutes).
Full baseline with peak RSS, index size, context latency, hardware, and
regression thresholds: [`docs/perf-baseline-v0.1.md`](docs/perf-baseline-v0.1.md).

| repo size              | cold index | no-change reindex | single-symbol edit | hybrid search |
|------------------------|-----------:|------------------:|-------------------:|--------------:|
| 804 files / 3,211 sym  | ~378 ms    | ~40 ms            | ~41 ms             | ~60 ms        |
| 2,404 files / 9,611 sym| ~1,069 ms  | ~126 ms           | ~153 ms            | ~170 ms       |
| 6,004 files / 24,011 sym| ~2,614 ms | ~385 ms           | ~314 ms            | ~430 ms       |

A one-symbol edit rewrites exactly its own embedding plus its enclosing
module (2 changed symbols; 24,009/24,011 embeddings reused at the largest
scale). Optimizations that produced these numbers:

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
code.

Results (21-task Tier A sample, Python+TypeScript, instance IDs pinned in
`eval-agent/results/tier_a_instances.txt`; run on an idle machine — eval
numbers degrade under concurrent load because failed embedding requests are
silently skipped):

| condition | file R/P/F1          | line R/P/F1     | tokens |
|-----------|----------------------|-----------------|-------:|
| lexical   | .607/.195/.295       | .555/.026/.049  | 3106   |
| vec-only  | .631/.282/**.390**   | .385/.163/**.229** | **1508** |
| hybrid    | .670/**.264**/.378   | .547/.042/.078  | 2780   |
| budgeted  | **.766**/.248/.374   | .422/.058/.102  | 1944   |

Honest reading: budgeted reaches hybrid-level file F1 at 30% fewer tokens
and wins line/symbol F1; vec-only keeps the best precision-per-token; hybrid
keeps the best line recall. Same-agent tier (headless opencode,
`opencode/x-preview-f-free`, 4 tasks x 4 conditions): all context conditions
reach gold-file utilization 1.00 vs stock 0.80; budgeted has the fewest
unnecessary edits (0.75) and fastest wall time (311s) at ~half of hybrid's
injected tokens. n is too small for causal claims. Full methodology:
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

OXIDE is designed as a context supplier for coding agents. The normal flow
needs no retrieval-specific orchestration:

```bash
oxide status --json
oxide index . --json
oxide search "where is authentication handled?" --json
oxide context --task "fix refresh token validation" --budget-tokens 4096 --json
```

Example machine-readable outputs:

```json
{"root":"/repo","index_exists":true,"is_current":true,"embedder_current":true,"files":42,"symbols":318,"embeddings":318,"embedder":"hashed-bow-256","supported_languages":["python","typescript","tsx"],"schema_version":1}
```

```json
{"scanned_files":42,"changed_files":0,"reused_files":42,"removed_files":0,"new_symbols":0,"changed_symbols":0,"deleted_symbols":0,"embedded_symbols":0,"reused_embeddings":318,"embed_failures":0}
```

```json
[{"id":"src/auth.py#AuthService.refresh_token","file":"src/auth.py","qualified_name":"AuthService.refresh_token","name":"refresh_token","kind":"method","language":"python","start_line":20,"end_line":42,"score":0.0312,"reasons":["lexical=1.234"],"snippet":"def refresh_token(token):"}]
```

```json
{"task":"fix refresh token validation","budget_tokens":4096,"used_tokens":38,"items":[],"omitted":[]}
```

Stable JSON contracts:

- `status` reports `root`, `index_exists`, `is_current`, `embedder_current`, indexed
  counts, `embedder`, `supported_languages`, and `schema_version`.
- `index` reports incremental scan/reuse/removal, symbol, and embedding counts.
- `search` returns an array of compact evidence records. Each record uses
  `id = path#qualified_name`, repository-relative `file`, symbol/name/kind,
  language/location, score, `reasons[]`, and `snippet`.
- `context` returns `task`, `budget_tokens`, `used_tokens`, `items[]`, and
  `omitted[]`; items use the same evidence fields plus `role` and
  `est_tokens`. The internal instruction-prefixed retrieval query is omitted.
- Runtime JSON failures are `{ \"error\": { \"code\", \"action\", \"message\" } }`
  with exit 1. `code` is one of a small stable set (`repository_not_found`,
  `no_source_files`, `index_missing`, `index_empty`, `index_stale`,
  `index_incompatible`, `index_unreadable`, `provider_mismatch`,
  `embedder_unavailable`, `index_failed`, `search_failed`, `context_failed`,
  `review_failed`, `status_failed`); `action` is one of `index`, `repair`,
  `retry`, `fall_back`, `stop` so a caller can decide what to do next without
  parsing `message`. Clap usage errors exit 2. Read commands open the index
  via the `immutable=1` SQLite URI parameter (never `SQLITE_OPEN_READ_ONLY`
  alone, which still creates `-wal`/`-shm` files against a WAL-mode
  database); they never create the index, never write to it, and never probe
  the embedder. Writes use `BEGIN IMMEDIATE` plus a shared `busy_timeout` so
  concurrent `oxide index` runs against an existing index serialize instead
  of racing.
- `src/service.rs` is the shared application boundary. A future MCP adapter
  can call it directly without duplicating CLI behavior.

`review --json` remains `{ range, changed_files[], changed_symbols[], related[] }`
for compatibility. Fewer symbols with stronger evidence beats more code.

### JSON migration note

Search JSON keeps its array shape and documented fields, and adds stable
`id`/`name`/`language` fields. It no longer serializes internal `Symbol`
fields such as `content_hash`, `imports`, `parent`, or `references`.
Context JSON keeps its pack shape but omits `query_used`, which was an internal
instruction-prefixed query; use `task` and each item's `reasons` instead.

### Future MCP reuse audit

An MCP adapter can call `RepositoryService` and reuse `StatusResult`,
`IndexResult`, `Evidence`, and `ContextResult` directly. It should not reuse
Clap parsing, human renderers, or duplicate index/retrieval orchestration.

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
