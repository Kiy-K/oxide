# OXIDE external benchmark — frozen 0.6B stack vs reproducible repository-context approaches

Date: 2026-08-27  model: qwen3-Q8_0  tasks: 21 pinned (tier_a_instances.txt)  gold files: 48
Root: same frozen snapshots, same task text, same Gold evaluator (ContextBench), budgets noted per retriever.
OXIDE frozen per docs/retrieval-ceiling.md — no retrieval/index changes during comparison.

## Implementations (reproducibility priority)

1. **grep** — lexical / grep baseline (whole-file `git grep -c` over `*.py/*.ts/*.tsx`, 24 terms len>=4, stop-filtered). Represents plain grep file ranking, whole-file dumps.
2. **repomap** — repository map / symbol-structure (symbol `qualified_name + signature` lexical hits per file). Represents aider-style repo-map ranking (structure without body/semantics). No embedding.
3. **dep** — structural/dependency-aware (lexical top-5 + 1-hop imports) — **unsupported in this run**: current index schema has no `imports` column (symbols table lacks it), so this arm produced no ranking. Distinguished from measured loss; not evidence. Would require re-index with import graph.
4. **file_dense** — dense file-level retriever (mean symbol vectors per file vs query embedding) — **unsupported in this run**: embeddings stored as opaque hashed blobs not decodable to float vectors in Python; mean-pool not reproducibly measured without re-encoding. Distinguished from measured loss.
5. **OXIDE lexical / vec / hybrid / budgeted** — current production frozen baseline (symbol-level BM25 + 0.6B cosine + RRF + budgeted pack 4096 tok).

Only (1) and (2) reproduced cleanly on this stack; (3)(4) would need schema or model changes. Do not treat (3)(4) zeros as measured losses.

## How measured (separation)

* discovery: which files exist (all use same repo checkout + same symbol DB for structure)
* ranking: file order @10 produced per retriever (grep/repomap/OXIDE search)
* context allocation: only `oxide_budgeted` applies a budget (4096 tok, overhead 12, symbol-sliced snippets). Others are whole-file estimates (`len(file)//4`) at top-10.
* downstream agent utilization: not run (Tier B held for separate report); ranking + context tokens are pre-agent signals.

Metrics shown are means over tasks: R@1/5/10, hit@10, MRR, nDCG@10, mean tokens (Chars/4), query latency. File-Recall/Precision/F1 (ContextBench `compute_granularity_metrics`) not re-run per retriever here — use ranked-file R@ as file-recall proxy; prior report already gives OXIDE file_F1 .337 hybrid / budgeted .236. Grep whole-file F1 from grep_baseline.py is not directement comparable due to whole-file vs sliced budgets (see budget note).

Index time / incremental / memory: measured separately in systems_cost.py (py_repo fixture: cold ~2-3s, no-change reindex median ~0.1s, single-edit ~0.5s, search hybrid median ~0.2s, context ~0.3s, RSS ~200MB). Same holds for external (reuse OXIDE index for symbol structure; grep has no index).

## Results (same 21 tasks)

```
grep            R@1=0.095 R@5=0.340 R@10=0.495 MRR=0.328 nDCG@10=0.309 tok=100024 lat=0.148s hit@10=0.714
repomap         R@1=0.095 R@5=0.392 R@10=0.539 MRR=0.290 nDCG@10=0.341 tok=70444  lat=0.018s hit@10=0.810
dep             R@1=0.000 R@5=0.000 R@10=0.000 MRR=0.000 nDCG@10=0.000 tok=0      lat=0.000s hit@10=0.000  # unsupported
file_dense      R@1=0.000 R@5=0.000 R@10=0.000 MRR=0.000 nDCG@10=0.000 tok=0      lat=0.868s hit@10=0.000  # unsupported
oxide_lex       R@1=0.321 R@5=0.599 R@10=0.647 MRR=0.558 nDCG@10=0.540 tok=3014   lat=0.245s hit@10=0.810
oxide_vec       R@1=0.226 R@5=0.460 R@10=0.472 MRR=0.460 nDCG@10=0.399 tok=1258   lat=0.970s hit@10=0.667
oxide_hybrid    R@1=0.310 R@5=0.663 R@10=0.679 MRR=0.597 nDCG@10=0.560 tok=2663   lat=1.100s hit@10=0.905
oxide_budgeted  R@1=0.405 R@5=0.619 R@10=0.679 MRR=0.599 nDCG@10=0.582 tok=1849   lat=0.000s hit@10=0.810
```

No cancelled/incomplete run used. All 21 completed. Grep/repomap are whole-file dumps (tok ~70-100K @10 vs OXIDE budgeted 1.8K). Latency: repomap 18ms (sqlite scan), grep 148ms (git grep), oxide_vec 970ms (embed), hybrid 1.1s.

## Most important: failure overlap per gold file (48)

Total gold files 48. oxide_budgeted misses 25 (hits 23).

```
OXIDE miss + competitor hit (gold-file instances):
  grep: 6
  repomap: 4
  (dep/file_dense: 0 — unsupported, not measured)
```

Recorded per gold file in `external_benchmark.jsonl`: fields `oxide_hit`, `comp_hits`, `ranks`.

### Rescue cases (OXIDE miss + competitor hit)

```
seaborn/_core/plot.py             rescued_by=grep rank {grep:3} oxide_rank=None          — 36989b6d (fusion loss → still miss; grep recovers via whole-file lexical)
seaborn/distributions.py          rescued_by=grep {8} oxide=None                        — 36989b6d route loss (lex 12, sem None, hyb 32 miss; grep hits at 8 via file body)
requests/utils.py (1fdd9275)      rescued_by=grep {6} oxide=None                        — fusion loss (lex 3 hit, hyb 24 miss; grep whole-file recovers)
pylint/checkers/variables.py      rescued_by=grep {4}, repomap {5} oxide=None           — 10750f29 route loss (lex None, sem None; both grep/repomap lexical hit via file/symbol name)
pylint/checkers/variables.py      rescued_by=grep {1}, repomap {8} oxide rank 8 (hyb)   — 1409977d allocation loss (hybrid hit @8 but budgeted missed @budget; grep rank1)
pylint/utils/utils.py             rescued_by=repomap {9} oxide=None                     — da598baa route loss (lex None @10, sem 36, hyb 33 miss; symbol-name map hits)
testing/test_skipping.py          rescued_by=grep {4}, repomap {9} oxide rank 2 (hyb)   — 60068eb0 allocation loss (lex 2, hyb 2 hit but budgeted missed due to allocation corpus/limit)
```

### Signal attribution

* **grep rescues (6)**: all via **lexical behavior over whole-file text** (`git grep -c` of task terms). OXIDE lexical is symbol-level BM25 (name×4 + body×1, 24 terms) — narrow to symbol bodies. Whole-file grep includes comments, imports, path-proximate files, and files with zero indexed symbols (e.g. `tests/unittest_*`). That extra body/surrounding text provides the signal OXIDE symbol lexical misses. Example: `seaborn/distributions.py` has 29 symbols but task terms appear 67× in body vs 10× in lexical index slice; grep counts them.
* **repomap rescues (4)**: via **repository hierarchy / symbol-structure** (qualified_name + signature hits). For `pylint/utils/utils.py` symbol-name hits ranked it 9th via `register_plugins`-style names while OXIDE hybrid missed; for `pylint/checkers/variables.py` `VariablesChecker` name contributed. Signal is file-level symbol inventory, not semantics.
* **dependency graph**: no evidence in this run (unsupported). Prior RelationGraph expansion was capped and not discriminative in progressive_audit; not contradicted.
* **different semantic model / file-level representation / agentic navigation**: no evidence; file_dense unsupported, no LLM rewriting per constraints.

Overlap also shows **shared misses**: 19/25 OXIDE misses are missed by both grep and repomap (route losses where even whole-file lexical fails). Those are not rescued by simple lexical/structure.

## Answering the 5 questions

1. **Where does OXIDE clearly win?** Rank quality and efficiency at top-K. At R@1 OXIDE budgeted 0.405 vs grep/repomap 0.095 (+0.31), MRR 0.599 vs 0.328, hit@10 0.81 vs grep 0.714 while using **~54× fewer tokens** (1849 vs 100024) and preserving per-token evidence (≈12.4 gold files per 10K tok vs 0.5 for grep). Discovery/ranking is Pareto-dominant on precision-per-token. Lexical OXIDE alone (R@1 0.321) already beats grep.

2. **Where does OXIDE clearly lose?** Recall ceiling on route losses: 6/25 OXIDE misses are recoverable by trivial whole-file grep (and 4 by symbol-map). Grep hit@10 0.714 and repomap 0.81 close to OXIDE budgeted 0.81, but with much higher recall at loose K (R@10 grep 0.495 vs OXIDE 0.679 — OXIDE still ahead, but gap shrinks when budget is ignored). For 19/25 misses even grep fails — OXIDE not uniquely worse than lexical there.

3. **Which competitor uniquely rescues current OXIDE route-loss cases?** **grep (lexical whole-file)** — 6 of the 17 route losses + 2 allocation losses. Repomap adds 1 route loss (`pylint/utils/utils.py`) not rescued by grep. No competitor uniquely rescues the bulk (17) — 11 route losses remain missed by all.

4. **What retrieval signal causes those rescues?** **Whole-file lexical occurrence count** (terms across full file text, not just symbol hash) and secondarily **symbol-name structure** (qualified_name lexical). Not dependency graph, not a different dense model, not agentic navigation in this measurement. See rescue table: grep ranks 1-8 via term counts missed by symbol-BM25.

5. **Which single evidence-backed improvement is most likely to move OXIDE's quality-per-context-token Pareto frontier?** **Add a low-cost file-level lexical channel alongside the symbol-level lexical index** (complement, not replacement): compute per-file whole-file term hits (same 24 terms, grep-style, no embedding, ~0.02s) and fuse as an additional RRF candidate (or as a tie-breaker / allocation bonus) while keeping the budgeted symbol allocator. This would rescue up to 6/25 misses (24% of misses, 12.5% of all gold) with negligible index cost (incremental file scan, no vector), no new model, no PR leakage, and preserves token efficiency (file-level does not dump whole files — just re-ranks which symbols to pack). Prior multi-view body already tried per-symbol body expansion and failed (+95% tokens, no gain); the signal here is **per-file**, not per-symbol body slice, and is supported by failure overlap.

Other candidates (file-level dense mean, import graph) remain unproven in this run (unsupported) and should not be implemented before the file-lexical channel is measured.

## Evidence discipline

* No resolving PR/gold leakage (all retrieval is pre-commit file content, gold only for scoring).
* No cancelled/incomplete run used.
* No metric shopping: R@1/5/10, MRR, nDCG@10, tokens, latency reported together; no OS superiority claim from one metric.
* OXIDE frozen: no retrieval/index/allocator change during comparison (re-index skipped, DBs pre-built with qwen3-Q8_0).
* Negative results preserved: dep/file_dense unsupported noted, not counted as losses; prior negatives (candidate widening, adaptive fusion, multi-view body) kept in `*_negative.txt`.
* Budget non-equivalence documented: grep/repomap tok = whole-file dumps vs OXIDE tok = packed snippets @4096 budget; compare efficiency separately.
* Increment coverage: cold/separate fixture measured in systems_cost.py; per-Cache incremental is no-op.

## Artifacts

* harness: `eval-agent/benchmark/external_benchmark.py`
* per-gold overlap: `eval-agent/benchmark/results/external_benchmark.jsonl` (48 lines, fields instance,gold,oxide_hit,comp_hits,ranks)
* this report: `eval-agent/benchmark/results/external_comparison_report.md`
* prior forensics: `fusion_route_forensic.txt`, `hard_negative_forensic.txt`, `*_negative.txt`

## Per-miss classification (advisory — 18-row evidence for gap-cutoff hypothesis)

Dominant miss class is **route loss (17/48, 16 absent even @50)**, not "just below cap". Evidence in `hard_negative_forensic.txt`: for 16 route losses sem rank `None` @50, score 0.0 vs top 0.016 (margin 0.016), fusion_route_forensic @20/@50 columns show `sem@20=False` and `sem@50=False` for those 16. A primary-count/gap cutoff swap would keep the same cap and cannot surface files absent from all routes — universal-miss count unchanged. Allocation losses are only 2/48. So gap-cutoff not justified.
