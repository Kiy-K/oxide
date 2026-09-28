# R1 Phase 1+ analysis plan

This file pins implementation details that PROTOCOL.md leaves open. It is
written and hashed **before any dev metric is computed**. It does not change
the frozen R1 rule, the exposure definition, the gates or the decision rule
(PROTOCOL sha256 `c88b49f1…7d83`, which still verifies).

## Corpora and provenance

| role | corpus |
|---|---|
| dev | the 5 committed-dump HEAD corpora (`retrieval-failure-taxonomy/results/corpora.json`): pylint `ba5c0c79`, pytest `79833c82`, zod `2bf7b063`, requests `611c6162`, flask `d73fa1cd` — 70 tasks |
| held-out | `heldout-clean.jsonl` (65 tasks), each at the **first parent** of its task commit (the #30 parent-commit convention) |
| ContextBench | the 21 `cb-tasks.jsonl` base commits |

- The #30 63-task parent list was machine-local and is not committed. It is
  reconstructed mechanically, below.
- **CB gold** comes from `EuniAI/ContextBench` `data/full.parquet` at
  `2b43909` (sha256 `2f56535b…`), the only revision containing that file.
  - The manifest pin `1436c28a` no longer contains `full.parquet`.
  - Path normalization follows `retrieval-failure-taxonomy/scripts/cbgold.py`.
  - Verified: per-instance gold-line counts match `pin21_rescore.jsonl`
    `line.gold_size` on 21/21 instances, and base commits match 21/21.
- **Binaries:** `oxide` and `examples/semantic_variant.rs` are built from
  `024afa5` in a research worktree. `src/` is unchanged. The only harness
  diffs are:
  - the R1 arm;
  - `--dump-texts` also printing file, kind, span and the D0 text.
- Indexing uses the production `oxide index` with the shipped default
  embedder (`arctic-embed-xs-q`).

## Arms

- **D0:** `semantic_variant --variant D0 --no-embed` on a `VACUUM INTO` copy
  of the production index. This is exactly production's stored vectors plus
  the production query path.
- **R1:** `semantic_variant --variant R1 --cache <r1 cache>` on a separate
  copy. It re-embeds every symbol through production `update_embeddings`.
  The cache is keyed by (fingerprint incl. `document_profile=variant:R1`,
  exact text hash). It only reuses identical R1 texts and never mixes with D0.

## Parity gates (must pass before any R1 metric is read)

- **(a) Provenance.** The D0 dumps must match the committed ranking-fusion
  dumps on dev (70 tasks), held-out HEAD corpora (ripgrep and httpx HEAD,
  plus pylint HEAD shared with dev; 15 tasks) and CB (21):
  - semantic top-200 keys, in order;
  - scores within 1e-6;
  - lexical top-200 keys in order;
  - the full fused order.
  - Any mismatch ⇒ STOP and diagnose.
- **(b) Harness.** A D0 re-embed through the harness (`--variant D0`, no
  `--no-embed`) must reproduce the production index's stored vectors
  bit-for-bit, on every dev corpus and on the first parent corpus of each
  held-out repo. Any mismatch ⇒ STOP.
- **(c) R1 text.** For every symbol of every corpus, the Rust R1 text must
  equal `file + " " + kind + " " + py_regex(rest)`, where `rest` is the D0
  text after `file + " " + kind + " "` and `py_regex` is the frozen Python
  rule. D0 text must equal `symbol_embed_text`. Any mismatch ⇒ STOP.

## Labels

- **dev / held-out.** Gold = the task's gold keys **present in that corpus's
  index** (from the `--dump-texts` symbol list).
  - Missing keys are flagged.
  - Parent-commit tasks can lose gold that the commit itself created.
  - A task with 0 present gold keys is **excluded as invalid-at-parent**, is
    listed, and is the mechanical counterpart of #30's 65 → 63.
  - Results are reported on the valid set. The "without known-invalid tasks"
    view is that same set; the "all" view has no meaningful metric for
    zero-gold tasks.
- **CB.** The gold symbols of a line are the innermost indexed symbol
  containing it: minimize (is_module, span length, symbol key). This is the
  taxonomy's oracle-gold rule.
  - Gold lines in unindexed files, or outside every symbol, are flagged and
    dropped.
  - CB exposure and ident apply PROTOCOL §2 unchanged to these gold symbol
    keys.

## Metrics (per task, then macro mean over tasks)

- **Semantic R@K** (K = 16, 50, 200): |gold ∩ semantic[:K]| / |gold|.
- **Gold semantic rank:** the best (minimum) semantic rank of any gold; a
  gold absent from the top-200 counts as 201 (censored). Reported as the
  median, and as a censored mean labeled as such.
- **Fused nDCG@10:** exactly `ranking-fusion-eval` / `semantic_eval.py`
  `metrics()` (binary gain, IDCG over min(|gold|, 10)).
- **Fused gold rank:** the best rank of any gold in the full fused list;
  absent counts as len + 1.
- **Final-pack coverage (secondary):** |gold ∩ pack item ids| / |gold|.
- **Strata:** exposed, ident and desc (PROTOCOL §2), and all.
- **Statistics:** paired Δ (R1 − D0) per task; task bootstrap, 2000
  resamples, `random.Random(32)`, percentile 2.5 / 97.5.
- **Gates:** exactly PROTOCOL §4/§5.
  - The dev decision is descriptive only, except that dev may REJECT.
  - Nothing is tuned.

## Cost

- **Tokens:** shipped tokenizer (`tok/`, tokenizers 0.22.2).
  - Pieces per symbol text for D0 vs R1, excluding special tokens.
  - Primary measure: uncapped mean over all symbols of all held-out parent
    corpora. Also reported: the 510-piece-capped mean (what the model
    sees), p95, and the per-set view.
  - Gate: mean +≤ 5 %.
- **Storage:** after full re-embeds of the same corpus under D0 and R1 (the
  parity-(b) corpora):
  - `SUM(LENGTH(vector))` of the embeddings table and the `VACUUM`ed db
    bytes. Gate: +≤ 1 %.
- **Latency:** `semantic_variant --bench-batch 1` docs/s for D0 vs R1, with
  no cache, on one parent corpus each of httpx, zod and ripgrep. The
  re-embed `embed_ms` values are also reported, with the cache noted.

## Order

1. Build all corpora and dumps.
2. Dev metrics are computed and written first; this plan and the scoring
   script are hashed before that.
3. Held-out, then CB, are scored with the same script, unchanged.
4. All held-out/CB dumps may be produced before dev is scored (dumps are not
   metrics). No held-out/CB metric is computed before dev is written.
