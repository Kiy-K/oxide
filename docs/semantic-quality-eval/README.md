# Semantic-retrieval quality research — the embedder track

Status: **paused 2026-09-21 after the audit and harness, before any
measurement; production unchanged.** Roadmap #9 defers this track behind
SQLite storage / request-path work. §4 records exactly what exists so a
later session can resume without redoing anything. The shipped pipeline
(`arctic-embed-xs-q`, `symbol_embed_text`, exact f32 dot product, RRF
K=60 at 0.6/0.4) stays frozen. Everything below runs through
`examples/semantic_variant.rs` against *copies* of production indexes, and
is scored by `scripts/semantic_eval.py`; results live in `results/`.

Why this track exists: the ranking/fusion research
(`docs/ranking-fusion-eval`) closed with frozen K=60 retained and two
challengers rejected, and left one failure class it could not touch —
**route loss on description-style queries**: the gold symbol is absent from
the semantic top-200 (R@10 0.071 on description-only queries under the
shipped embedder), so no fusion or reranking can recover it. That is a
candidate-generation problem in the semantic channel, and it has two
possible causes: the model, or the text the model is given.

## 1. Audit of the shipped semantic pipeline

Everything in this section is read from `src/embeddings.rs`,
`src/index.rs`, `src/retrieval.rs`, and one production index
(`httpx` at `336204f0`, 1,409 symbols), and checked against the model
card for `Snowflake/snowflake-arctic-embed-xs`.

| stage | what ships | audit finding |
| --- | --- | --- |
| model | `arctic-embed-xs-q`: `snowflake-arctic-embed-xs` (22M params, 384-d, BERT/MiniLM-L6 architecture, 512-token window), int8 ONNX via fastembed, CLS pooling | matches the model card (CLS, 512 tokens, "based on all-MiniLM-L6-v2"). Trained on web query→passage pairs: the passages it learned to match are natural-language text. |
| query prefix | `"Represent this sentence for searching relevant passages: "` prepended to the query, nothing on documents (`native_model_spec`) | exactly the model card's convention ("use the query prefix below (just on the query)"). 10 tokens of a 512 budget. |
| document text | `symbol_embed_text` = `file kind qualified_name signature imports references`, whitespace-joined | **identifiers only.** No docstring, no comment, no body line. The path is ~12 tokens of a ~35-token identity head; the rest is import and reference *names* (p50 5, p90 17 per symbol). Token length p50 64, p90 114, none truncated at 512. A description-style query ("retry the request when the connection is reset") has to be matched against `httpx/_transports/default.py function map_httpcore_exceptions ...` — text the model never saw during training. |
| normalization | fastembed L2-normalizes; stored vectors have ‖v‖ ∈ [0.999999, 1.000000] | dot product ≡ cosine, as the model card prescribes. |
| scoring | exhaustive sequential-f32 dot over every stored vector, bounded top-200 heap, `cmp_score_id` tie-break | exact, deterministic; no approximation to audit. |
| index compatibility | `embedding_fingerprint` (model, quantization, query/document profile, pooling, normalization, dimension) keyed in meta; a change triggers a full re-embed via the migration marker | any document-text change is a **new embedding space**: it must bump `document_profile` in the fingerprint so existing indexes re-embed, and it changes `content_hash(symbol_embed_text)` for every symbol. |

Where the lexical channel differs: BM25 indexes symbol *bodies* at weight 1
(AGENTS.md: "bugfix targets hide behind local identifiers"), so BM25 can see
a docstring word or a string literal; the semantic channel cannot. That is
the asymmetry this track tests.

## 2. Hypotheses and challengers

**H1 (text):** the semantic channel is weak on description-style queries
because the document text contains no natural language, not because the
model is too small. Test: re-embed the same corpora with the same model
under document-text variants and re-run the frozen pipeline.

| variant | document text | what it isolates |
| --- | --- | --- |
| D0 | `file kind qualified_name signature imports references` (production) | control; must reproduce production bit-for-bit |
| D1 | D0 + the symbol's own docstring / leading comment block (≤600 chars), placed before imports | natural language, nothing else changes |
| D2 | D1 + first 10 non-empty lines of the span (≤800 chars) | body identifiers and string literals |
| D3 | `kind name qualified_name signature doc body path-words` — **no imports/references** | whether import/reference name lists dilute the CLS representation |
| D4 | `kind qualified_name signature doc` — identity + doc only | minimal text; whether path and references carry any signal |
| D5 | `doc body` only (falls back to D0 when both are empty) | a pure natural-language/body view, the upper bound of "text, not model" |
| D0 + bare query | production documents, no query prefix | whether the asymmetric prefix helps or hurts on code |

**H2 (model):** a different small Rust-native model, on the same text,
recalls more. Candidates are the profiles already wired into
`native_model_spec` (no new runtime surface): `arctic-embed-s` (33M, fp32,
e5-small base), `bge-small-en-v1.5-q` (33M, int8), `minilm-l6-v2-q` (22M,
int8, the shipped model's own base without Arctic's retrieval fine-tune).

## 3. Method

- Tasks: the 65 repository-balanced held-out tasks from
  `docs/ranking-fusion-eval/results/heldout-clean.jsonl` (httpx, requests,
  flask, ripgrep, zod, pytest, pylint; gold = symbols the commit changed,
  query = commit message). ContextBench's 21 human-labeled instances are
  used as the second check for the leading variants.
- Corpora: one worktree per task commit, indexed with the production
  binary at `5c62edd` and the shipped embedder. (The earlier research
  built some corpora at a moving `HEAD`; here every task, including those,
  is indexed at its own commit — so the baseline numbers below are
  re-measured on these corpora, not copied from the ranking report.)
- Harness: `semantic_variant` snapshots the production index
  (`VACUUM INTO`), re-embeds it through the production `update_embeddings`
  path with a wrapper provider that substitutes the variant text, then dumps
  the BM25 top-200, the variant semantic top-200, the production RRF fusion
  of the two, and the production context pack — the same record
  `fusion_dump` emits. Self-check: `--variant D0` reproduced
  `fusion_dump`'s lexical, semantic, fused, expanded and pack lists
  identically on `httpx-336204f0`.
- Metrics (`scripts/semantic_eval.py`, definitions identical to
  `fusion_eval.py`): semantic-only R@10/50/200 (candidate recall — the
  route-loss lever), fused R@5/10/20, nDCG@10, MRR, gold-in-pack, relevant
  tokens per 1k pack tokens, loss partition (route / ordering / allocation
  / hit), macro-averaged R@10 over repositories, paired bootstrap 95% CIs
  vs baseline (2000 resamples). Strata: `desc` = no gold symbol name
  appears in the query (description-style), `ident` = otherwise.
- Cost: embed wall time per corpus, model load time, index bytes, query-path
  timings, peak RSS.

## 4. State at pause (no results yet)

What is done and verified:

- The audit in §1 (the one finding that matters: the semantic document is
  identifiers only, and the model was trained on natural-language
  passages).
- `examples/semantic_variant.rs` builds clean under clippy and passed its
  self-check: `--variant D0` on `httpx-336204f0` reproduced
  `fusion_dump`'s lexical (200), semantic (200), fused (307), expanded
  (309) lists and the context pack identically, so the harness measures
  only what it changes.
- `scripts/semantic_eval.py` runs end to end on a probe dump.
- Cost probe on that one corpus (1,409 symbols, 16 threads, machine
  otherwise loaded): D0 re-embed 15.5 s (~91 symbols/s), D1 16.3 s,
  D5 18.5 s, D3 21.2 s; 312/1,409 symbols (22%) have a docstring or
  leading comment under the D1 extractor. Model load 150–200 ms.
- Corpora: 12 of 65 held-out worktrees indexed (57,826 symbols) under
  `~/.cache/oxide-semantic-eval/wt/` before the pause; the build script
  (`build_corpora.sh`), variant driver (`run_variant.sh`, `run_cb.sh`) and
  the run queue (`queue.sh`: baseline, D1, D3, D5, D0+bare query, D4, D2,
  then `arctic-embed-s`, `bge-small-en-v1.5-q`, `minilm-l6-v2-q` on D0)
  live next to them. All are idempotent and resume where they stopped.
  They are machine-local scratch, not repository artifacts.

What is **not** evidence: a single-task probe (one httpx task) moved the
gold's semantic rank from 66 (D0) to 66 / 103 / 147 under D1 / D3 / D5.
One task says nothing; it is noted only so nobody mistakes it for a
result.

Estimated cost to finish: ~50 min to index the remaining 53 corpora,
then 35–60 min per queue entry on this machine (embedding is CPU-bound
at ~50–90 symbols/s; document-text variants are longer). ContextBench's
21 corpora are already indexed under `~/.cache/oxide-contextbench/repos`
and `run_cb.sh` reuses them.

## 5. Verdict

None yet — no challenger has been measured. Production keeps
`arctic-embed-xs-q` with the identifier-only `symbol_embed_text`. If the
track resumes, the decision gate is the one every other track used:
a challenger ships only if it improves description-style candidate recall
*and* fused nDCG on the balanced held-out set without regressing the
fixture benchmark, ContextBench, embed throughput, RSS, or index size —
and any document-text change is a new embedding space (full re-embed on
upgrade, `document_profile` bump in the fingerprint).

## 6. Reproduce

```
cargo build --release --example semantic_variant
sqlite3 <repo>/.oxide/index.db "VACUUM INTO 'copy.db'"
semantic_variant <repo> tasks.jsonl --db copy.db --variant D1 [--profile arctic-embed-s] > dump-D1.jsonl
scripts/semantic_eval.py tasks.jsonl dump-baseline.jsonl D1=dump-D1.jsonl --md
```
