## Lead (from #42; not yet validated)

Tracked by #9. In #42, arm **B** (the shipped flat selector *without* the 5-primary and 2-per-file caps, packed to exactly the shipped pack's `used_tokens`) beat the shipped selector **A** on the 149 frozen #40 tasks:

| | Δ gold-line coverage (repo-clustered 95 % CI) | rel/used | up/down |
|---|---|---|---|
| pooled (149 tasks, 51 repos) | **+0.0527 [+0.0137, +0.0975]** | **+13.5 % [+3.2 %, +28.6 %]** | 30/7 |
| held-out (80, 8 repos) | +0.0934 [+0.0295, +0.1743] | +33.5 % | 14/1 |
| ContextBench (69, 45 repos) | +0.0055 [−0.0023, +0.0164] | +11.4 % [+0.9 %, +28.2 %] | 16/6 |

Source: `docs/treeselect-eval/results/` (`per_task.tsv`, `result.txt`, the `B vs A` comparison).

**Why it is a lead, not a result**
- It was a reported, non-gated comparison, and it is in-sample for this task set.
- It is held-out-driven: ContextBench's coverage CI includes 0.
- It differs from the allocator studies' rejected top-ups (ch1, ch5, ch7). It **reallocates slots at the same tokens** instead of adding tokens. ch5 failed E1 because it spent extra tokens; here efficiency rises.

**What it would take** (not started; needs its own preregistration):
- Freeze B's exact rule. It is the role-then-score order with the 1-test cap, dedup, floor and shrink-to-fit unchanged. Because the caps no longer bound the pack, a budget or stop rule has to be decided: either keep the shipped 4096 budget with a stop rule, or define a token target.
- Evaluate once on **fresh** tasks that played no part in #42, with ch5-style gates (coverage CI > 0, efficiency lower bound ≥ −5 %, per-stratum non-negative, latency/RSS, byte parity).
- Keep this separate from TreeSelect v1, which is being diagnosed on the #42 tasks as a development set.

Production is unchanged.


## Preregistered validation (added 2026-10-04, before any fresh task exists)

TreeSelect is stopped: the #42 v1 diagnosis (`docs/treeselect-eval/v1-dev/`) found no hierarchy lever at this seam. This issue validates the one effect with a development signal.

**Research only. Production unchanged.** Retrieval, RRF (K=60, 0.6/0.4, depth 200), embeddings, `symbol_embed_text`, the schema, ids and the CLI/MCP/JSON output stay frozen. The arms run through #42's committed, never-merged `docs/treeselect-eval/research.patch`, applied unmodified (arm B's code path did not change in amendment 1).

### Question

**At equal tokens, does removing the 5-primary and 2-per-file caps from the shipped selector improve final-context quality on fresh tasks?**

### Arms (frozen; exactly #42's definitions)

| arm | rule |
|---|---|
| **A** | the shipped selector (16 seeds, budget 4096) |
| **B** | the shipped pipeline (same seeds, production expansion and evidence, dedup, floor, role-then-score order, 350-token windows with shrink-to-fit, 1-test cap) **without the 5-primary and 2-per-file caps**, with the budget set per task to A's `used_tokens` |

Harness: `examples/treeselect_pack.rs --arms A,B` from that patch, using the archived #42 amended binary.

### Fresh data

- **Held-out.** The same 8 upstream clones as #40/#42. `make_tasks.py` runs unmodified with `max_tasks=20` per repo, in its git-log order. Exclusions come from #40's `excl.py`, unmodified, run at this protocol's frozen commit: every SHA, parent and instance in any committed `docs/**/*.jsonl[.gz]` / `eval-agent/**/*.jsonl` file, which includes all of #40's and #42's tasks and draws. Then, as #40: parent corpora, ch5's parent gold filter and near-duplicate rule, edit-locus gold, fallback exclusion, #40's eligibility rule, and the **first 10 eligible tasks per repo**.
- **ContextBench.** The pinned parquet (`2f56535b…`), excluding every instance id in that committed text. #40's per-stratum round-robin draw runs unmodified with **seed 43** and the same targets (description 25, quoted 34, other 10), the same checkout, index and eligibility rule, and line gold from `gold_context`.
- **Population.** #40's eligibility rule is kept so that the fresh population matches the development population. It keeps tasks whose gold files appear in, but do not fill, the fused top-50 file view.
- **Repo identity.** A repo is the lowercased upstream `owner/name`, and the same upstream in both sets is one repo. Repos may overlap the development set; tasks may not.
- **Generation scripts** (`docs/cap-removal-eval/scripts/`) are committed with this protocol before generation. They change only paths, the seed and the exclusion source.

### Metrics, CIs and gates

- Metrics are #42's: `score_alloc.pack_metrics` with each task's line gold. CIs use #42's repo-clustered paired bootstrap (repos with replacement, then tasks within each drawn repo; 10,000 resamples; a fresh `numpy.random.default_rng(0)` per analysis; repos sorted).
- E1's relative efficiency is ch5's definition, computed within each resample. Tasks where A used 0 tokens are dropped from efficiency.
- A task-level bootstrap (10,000, seed 0) is reported as descriptive only.

| gate | rule (B vs A) |
|---|---|
| Q1 | pooled gold-line coverage Δ: 95 % CI lower bound > 0 |
| Q2 | neither stratum (held-out, ContextBench) has a negative point Δ |
| E1 | pooled rel/used relative Δ: CI lower bound ≥ −5 % |
| R1 | coverage regressions ≤ improvements; every regression listed with its cause |
| O1 | median per-task Δ context time (min of 2 runs) ≤ 5 % of median search time; peak RSS Δ ≤ 5 % on the 3 largest corpora (separate `--arms A` vs `--arms A,B` processes, alternating, 2 reps) |
| P1 | with the switch off, every task's `oxide context --json` is byte-identical to the frozen binary's, and two runs of B give identical packs |

**Validity.** At least 50 held-out and 50 ContextBench tasks, at least 6 held-out repos, and P1 passes.

**Disposition**
- **PASS**: every gate passes. The only next step is a production-design issue (how to set the token target without running A), with its own evaluation.
- **REJECT**: Q1, Q2 or E1 fails.
- **INCONCLUSIVE**: validity fails, or the harness deviates from this protocol.
- **NOT PASSED**: Q1, Q2 and E1 pass but R1 or O1 fails.

No per-task score is inspected against gold before the full run is scored.

