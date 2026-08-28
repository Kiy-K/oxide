# Canonical baseline — frozen production code (reverted)

Date: 2026-08-27
Git hash: d1076f5318587fa4deb7e3d329f0f844a6f26cf5 (reverted, no retrieval/index changes)
Embedder: qwen3-Q8_0 @ http://127.0.0.1:8191/v1/embeddings (0.6B Q8)
Config: [`src/config.rs`](../src/config.rs) is the authoritative production setting set (RRF 60, lexical/semantic 0.6/0.4, strong-seed 0.55, 4 chars/token, 4096-token default budget).
Tasks: 21 pinned tier_a_instances.txt (7 repos)
Index: ~/.cache/oxide-contextbench/repos/*/.oxide/index.db (incremental, already built)

Reproduced fresh (this commit, no code change):

```
tasks=21 model=qwen3-Q8_0
cond          R@1    R@3    R@5   R@10   hit@5     MRR  nDCG@10    tok  items
lexical     0.321  0.540  0.599  0.647   0.76  0.558   0.540  3014  10.0
vec         0.226  0.413  0.460  0.472   0.67  0.460   0.399  1258   9.5
hybrid      0.310  0.579  0.663  0.679   0.86  0.597   0.560  2663  10.0
budgeted    0.405  0.532  0.619  0.679   0.76  0.599   0.582  1849   6.7
```

The pre-revert 0.429/0.654/0.637 report is not current production and must not be used as a ruler.

Canonical ruler for file-level channel evaluation is the table above (budgeted R@1 0.405, MRR 0.599, nDCG 0.582, tok 1849, hybrid File-F1 ~0.33 per earlier). Subsequent comparisons must use same `eval-agent/benchmark/ranking_metrics.py` and same `tier_a_instances.txt`.
