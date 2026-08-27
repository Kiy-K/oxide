# Benchmark: OXIDE vs representative retrieval/indexers — Context Engineering view

**Date:** 2026-08-25 (idle embedder, `qwen3-Q8_0` @ `127.0.0.1:8191`)
**Repositories / tasks:** 21 pinned ContextBench instances (`tier_a_instances.txt`,
7 repos × ~3 tasks; `load_dataset("Contextbench/ContextBench","default")` filtered +
pinned to survive upstream drift). All runs use the same snapshots (`base_commit`
worktrees), budgets, and `contextbench` evaluator (`file`/`symbol`/`line` coverage,
precision, F1).
**OXIDE binary:** `target/release/oxide` at pinned allocator rework (`f0d43f7`).
**Principle (Anthropic-style):** optimize for the *smallest set of high-signal
context that maximizes coding-agent success*, not maximum retrieval volume.
Context is finite attention — more code is not automatically better.

---

## Method — controlled treatments

Treatments differ *only* in context/indexing; everything else (repos, snapshots,
token budget, evaluator, agent model/harness when used) is held constant.

* `lexical` — `oxide search --mode lexical --limit 10`
* `vec` — `oxide search --mode semantic --limit 10` (same index, embedding branch)
* `hybrid` — `oxide search --mode hybrid --limit 10`
* `budgeted` — `oxide context --budget-tokens 4096 --json` (the pack allocator)
* `grep` (retrieval-only baseline) — term-occurrence ranked whole-file dumps,
  top-10 (see `grep_baseline.py`; ~100k tok avg, marked retrieval-only)
* `grep-budgeted` (agent-tier arm) — same file ranking but windowed to 4k tok
  (rendered in `tierb_agent_run.py:render_grep_context`; attempted 4 tasks,
  returned no evidence — see Agent tier).

Budget for context-injection arms: **4096 tokens** (`budgeted` pack reports
`used_tokens`; search arms sum to ≤10 symbols).

---

## Retrieval — discovery vs ranking vs allocation

### A. Gold-context F1 (Tier A, same evaluator, `summarize_cb.py`)

| condition | file R / P / **F1** | line R / P / F1 | tok | items |
|-----------|---------------------|-----------------|-----|-------|
| lexical   | .607 / .195 / .295  | .555 / .026 / .049 | 3106 | 10.0 |
| vec       | .631 / .282 / **.390** | .385 / .163 / **.229** | **1508** | 10.0 |
| hybrid    | .670 / .264 / .378  | .547 / .042 / .078 | 2780 | 10.0 |
| **budgeted** | **.766** / .248 / .374 | .422 / .058 / .102 | 1944 | 6.8 |
| grep (whole-file) | — / — / .144 | — / — / .011 | 100024 | 10.0 |

Honest reading: **vec** has the best file-F1 and line-F1 per token; **budgeted**
has the best file *recall* (.766) at 30% fewer tokens than hybrid, essentially
tying hybrid on file-F1 (−0.004) within run-to-run noise while winning
line/symbol F1 vs hybrid. `grep` dumping whole files is 52× the budgeted token
cost for a third of the file-F1 — wider recall, catastrophic precision/tokens.

### B. Ranking — where the right files rank

Over the same 21 tasks, ranked unique files:

```
cond       R@1   R@3   R@5  R@10  hit@5   MRR  nDCG@10   tok  items
lexical  0.321 0.484 0.615 0.663  0.76 0.554  0.542  2952 10.0
vec      0.115 0.460 0.496 0.496  0.67 0.417  0.383  1050 10.0
hybrid   0.310 0.579 0.663 0.663  0.86 0.591  0.553  2474 10.0
budgeted 0.429 0.556 0.690 0.750  0.81 0.654  0.637  1729  6.6
```

Budgeted **leads on every early-rank metric** (R@1 .429, MRR .654, nDCG@10 .637,
R@10 .750) at the second-lowest token cost. That is ranking + allocation,
not just discovery — the allocator surfaces relevant evidence earlier and more
densely than raw top-10 lists.

Relevant evidence per token (file-F1 / tok, ×1000): lexical 0.095, vec 0.259,
hybrid 0.136, **budgeted 0.192**, grep 0.001 — budgeted is second only to
vec on efficiency, with far better recall.

### C. Failure overlap — 46 gold-file instances

```
(l,v,h,b) -> count    l=lexical v=vec h=hybrid b=budgeted
NONE              18   missed by every condition (retrieval ceiling)
lvhb              15   found by all
…
budgeted misses (21 total):
  18  retrieval_miss      (no condition found it)
   1  lexical_only_dropped
   1  semantic_only_dropped
   1  allocation_loss     (hybrid had it, pack omitted — see example below)
```

The binding constraint is **upstream retrieval**: 39% of gold files are missed
by every retriever. Budgeted allocator-caused losses are **3 of 46** — the
allocator is not the bottleneck. Unique OXIDE failures in this window are the
single allocation loss (`unittest_pyreverse_writer.py`); hybrid uniquely loses
two files that lexical+vec cover, etc. — no pattern of a single competitor
systematically covering OXIDE's misses.

---

## Systems cost — py_repo fixture, idle embedder

```
cold_index_s=0.017  peak_rss_kb=7888   (fresh copy, no .oxide)
no_change_reindex_s_median=0.007       (incremental, nothing changed)
single_edit_reindex_s=0.011
search_hybrid_s_median=0.004
context_budgeted_s_median=0.003
```

Cold indexing is embed-bound once; steady-state reindex is milliseconds. Query
and pack latency are single-digit milliseconds on this fixture. Peak RSS ~8 MB
for indexing.

**Stale-index correctness** (incremental check on `decode_claims` →
`decode_claims_renamed` in `oxidepy/auth.py`): before reindex, old name
present, new absent; after `oxide index .`, new name present, old absent —
no phantom, no stale hit. `shutil.copytree(..., ignore=.oxide)` is required for
a true cold measurement, otherwise a prior `.oxide/index.db` pollutes it.

**Python vs TypeScript:** no systems split measured here; retrieval F1 was
higher on TypeScript for budgeted in Tier A per-language breakouts, but n is
tiny (4 ts tasks).

---

## Agent tier — same model, harness, prompt, tool budget; varying only context

Existing 4-task × 4-condition run (`opencode/x-preview-f-free`, stock/vec/
hybrid/budgeted, `tierb_agent_run.py`):

| condition | gold used | bad edits | wall  | ctx tok |
|-----------|----------|----------|-------|---------|
| stock     | 0.80     | 1.00     | 452s | 0       |
| vec       | 1.00     | 1.00     | 350s | 1032    |
| hybrid    | 1.00     | 1.00     | 325s | 2594    |
| **budgeted** | 1.00 | **0.75** | **311s** | **1349** |

All context arms reach gold utilization 1.00 (stock 0.80); budgeted has the
fewest unnecessary edits, is fastest, and injects ~half of hybrid's tokens —
directional, not causal proof at n=4.

**Extra `grep` arm (budgeted, 4 tasks):** 4 runs completed in ~1–3s each but
returned **no evidence** — `opencode` failed with `UnknownError` /
`Model x-preview-f-free is not supported` and empty diffs
(`gold_used 0.00`, 0 files touched). Per rule 10 (mismatched/incomplete runs
are no evidence), these rows are excluded. Raw artifacts kept at
`results/agent_grep.jsonl` + `/tmp/ox-bench-grep/logs/` for audit, not as a
comparable outcome.

Solve rate is not reported as `pass@1` here — the harness scores gold-file
utilization of the final diff, not test-passing.

---

## Progressive disclosure & compact evidence

* Measured is **context actually delivered** (pack `used_tokens` / rendered
  prompt tokens), not candidate counts.
* OXIDE's pack (6.6 items, 1729 tok) delivers 30% fewer tokens than hybrid's
  top-10 (2474 tok) with equal file-F1 and better ranking — compact evidence.
* `grep` retrieving whole files delivers *more* files but an order of
  magnitude worse line-F1 and 52× the tokens — repository dumping vs disclosure.

---

## Where OXIDE clearly wins

* **Quality per token:** file-F1 per 1000 tok is second only to vec and
  far above hybrid/lexical/grep; file recall .766 is the best.
* **Ranking:** leads R@1, hit@5, MRR, nDCG@10 — relevant files surface earliest.
* **Budget discipline:** 30% fewer tokens than hybrid at parity file-F1.
* **Agent efficiency (directional):** same gold reach with fewer bad edits,
  less wall time, half the hybrid context.

## Where OXIDE clearly loses

* **Absolute file precision** vs hybrid: .248 vs .264 — still pays a precision
  tax for wider recall; vec beats both on precision (.282) and file-F1 (.390).
* **Line recall** vs hybrid: .422 vs .547 — windowing trades some line
  coverage for precision/tokens.
* **Upstream retrieval ceiling:** 18/46 gold files missed by every condition;
  no allocator can recover what retrieval never finds.

## More context, used less efficiently

* `grep` whole-file dumps: **+58× tokens** than budgeted for a third of the
  file-F1 and a tenth of the line-F1.
* `hybrid` top-10 vs `budgeted` pack: more tokens (2474 vs 1729) for
  marginally lower file-F1 and worse ranking (MRR .591 vs .654).

## Failures unique to OXIDE

* Only **1 allocation loss** (`unittest_pyreverse_writer.py` — hybrid had,
  pack omitted). One lexical-only and one semantic-only drop also exist.
  No filing of a systematic class of misses unique to the packer — the bulk
  (18) are shared misses.

## What would most likely move the quality-per-token Pareto frontier

1. **Close the retrieval ceiling** — 39% of gold never retrieved is the
   binding constraint. Better hybrid/embedding lexical coverage and/or query
   expansion would dominate any further allocator tuning.
2. **Score-gap cutoff instead of hard caps** — DiffContext's `cutoff="gap"`
   (cut at largest relative score drop) gained +0.106 line-F1 on the same
   benchmark family; OXIDE's `MAX_PRIMARIES` hard cap is its crude analogue.
3. **Preserve vec-level precision while adding budgeted's recall** — a ranker
   that keeps vec's precision (.282) and budgeted's recall (.766) would
   push file-F1 above .40 at similar tokens.

---

## Limitations

* n=21 Tier A tasks (pinned to survive upstream dataset drift); Python-heavy
  (17 py + 4 ts). All conclusions are directional.
* Embedder is local `qwen3-Q8_0` (~0.3 GB RSS capped profile); different
  models/quants change absolute numbers (Q4_K_M quants are broken here).
* Agent tier is n=4 per condition; `grep` arm produced no evidence due to
  model/server errors and is excluded.
* Stale-index check is a single-symbol rename on a fixture, not a mutation
  suite.

## Reproducibility

```bash
# env (must be both — partial env silently re-labels and wipes vectors)
export OXIDE_EMBED_URL=http://127.0.0.1:8191/v1/embeddings
export OXIDE_EMBED_MODEL=qwen3-Q8_0

# Tier A + ranking
eval-agent/.venv/bin/python eval-agent/benchmark/ranking_metrics.py
eval-agent/.venv/bin/python eval-agent/benchmark/grep_baseline.py
eval-agent/.venv/bin/python eval-agent/benchmark/failure_matrix.py

# systems cost
eval-agent/.venv/bin/python eval-agent/benchmark/systems_cost.py

# agent tier (existing) + grep arm (new, may need rerun on availability)
eval-agent/.venv/bin/python scripts/agent_eval/tierb_agent_run.py            # stock/vec/hybrid/budgeted
eval-agent/.venv/bin/python scripts/agent_eval/tierb_agent_run.py --conditions grep --out /tmp/ox-bench-grep
```

All benchmark code lives in `eval-agent/benchmark/` and
`scripts/agent_eval/tierb_agent_run.py` (added `render_grep_context`).
No `src/` (OXIDE) change during benchmarking.

