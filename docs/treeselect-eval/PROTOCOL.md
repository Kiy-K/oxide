## Parent and motivation

Tracked by #9. Inspired by PageIndex's value-based tree search (notes: `docs/treeselect-eval/pageindex-notes.md`, local and not yet committed). The node score and collapse rule below are **PageIndex-inspired adaptations**, not PageIndex's production policy. PageIndex publishes its value rule as a chunk-similarity aggregate that feeds an LLM tree search, and its production search policy is not open source.

**Research only. Local only. No Jev, no LLM and no remote calls.** These stay frozen: retrieval, RRF (K=60, 0.6/0.4, depth 200), embeddings and `symbol_embed_text`, the production allocator, the schema, symbol ids and the CLI/MCP/JSON output. The prototype is a research switch in a never-merged `research.patch`, the same way `alloc_research` was. With the switch off, output is byte-identical to production.

Prior evidence says the selection seam is where the loss is, and that the signals tried there so far found nothing:

- The selection seam holds 84 % of natural lost weight. The oracle allocator ceiling is D +0.22 and B16 is +0.15 (`docs/retrieval-failure-taxonomy/` §13).
- #32 found no transferable per-candidate signal (held-out AUROC 0.848; LR-ALL +0.013, CI includes 0). Its features included `same_file_top1`/`top5`, but **no aggregate file or directory evidence mass**.
- ch5 (a per-file top-up) was rejected on its fresh set by the efficiency gate E1 (lower bound −13 %). ch1 showed that spending more budget buys coverage at worse token efficiency.
- One-hop structural neighbors are 0.1–2.3 % gold-precise.

TreeSelect tests the one thing these studies left out: **selection ordered by hierarchical evidence mass instead of flat score order with count caps.**

## Question

**Does hierarchical deterministic selection improve final-context quality over the current selector, at the same token cost?**

This issue tests one instance of that question, **TreeSelect v0**. Its verdict covers TreeSelect v0 only, not hierarchical selection in general.

## Pipeline (research arm `T`)

```
frozen retrieval (same 16 seeds, same evidence-coordinator candidates)
  → repo / directory / file / symbol tree
  → deterministic TreeSelect
  → bounded graph expansion
  → budget packing
```

The **TreeSelect v0** rule is frozen by this issue. A change after the first scored run counts as a new arm and is reported as post-hoc.

1. **Pool.** The production pool after dedup, without the RelationGraph expansion step (it moves to step 5). Production dedup (module subsumption, >80 % overlap) and the relevance floor (0.15 × top seed score) apply unchanged.
2. **Tree.** Root → the file's parent directory → file → symbols, with symbols nested by span containment. A node with a single child is flattened into it (adapted from PageIndex's dynamic flattening). A `Module` candidate is a leaf of its file node.
3. **Value.** Each leaf scores its unchanged candidate score. Every node scores `NodeScore(v) = Σ leaf scores under v / √(N_v + 1)`, where N_v is the number of leaves under v. This is adapted from PageIndex's published value-based node score: OXIDE's frozen candidate scores take the place of chunk similarities, and every node, leaves included, is scored this way so that all nodes are comparable on one scale.
4. **Selection.** Best-first. The frontier starts with the root's children. Pop the highest NodeScore (ties go to the lower path, then the lower symbol id). Opening an internal node pushes its children; popping a leaf offers it to the packer.
   - **Collapse.** If a container (a file `Module` symbol when the index has one, or a class) has ≥ 2 pooled leaves and its full body is ≤ `CONTEXT_PER_ITEM_TOKEN_CAP`, it is offered once as a whole instead of its leaves. This is a simplified token-cost adaptation of PageIndex's subtree-merge idea (when the parent is cheaper than routing to its children, take the parent), not its tree-optimization procedure.
5. **Bounded graph expansion.** One-hop `RelationGraph::neighbors` of the first 5 *selected* leaves, with production's constants: 2 per anchor, 2 in total, score anchor × 0.4, role from `Role::assign`. The production pipeline expands from the top-5 *seeds* instead.
6. **Budget packing.** Production's fill loop: `render_snippet`, the 350-token cap with shrink-to-fit, 12 tokens of overhead and the 1-test cap (measured 0 gold loss). The **5-primary cap and the 2-per-file cap are replaced** by the tree order and the budget. Output order and the JSON shape stay the same as production (role, then score).

## Arms

| arm | what | gating |
|---|---|---|
| **A** | shipped selector (cached #40 frozen binary; `src/` has not changed since `22da600`) | baseline |
| **B** | allocator-only control: the current flat selector with production graph expansion (top-5 seeds, before selection), the same pool, dedup, floor, role-then-score order, 350-token cap and 1-test cap, but **without the 5-primary and 2-per-file caps**, token-matched to A's `used_tokens` | control |
| **T=−x** | TreeSelect v0 steps 1–4 and 6, but with **production graph expansion** (top-5 seeds, before selection, its neighbors entering the tree as leaves) instead of step 5; token-matched to A | **gated treatment** |
| **T=** | full TreeSelect v0 (step 5 expansion from selected leaves), token-matched to A | full v0 |
| T4096 | TreeSelect v0 with the full 4096 budget; stops at the floor or when the budget runs out | descriptive |
| T=−c | T= without collapse | descriptive ablation |

Comparisons:

- **T=−x vs B is the primary, gated hierarchy comparison.** The two arms see the same pool, the same production expansion and the same tokens, and neither has count caps. The only difference is the TreeSelect v0 ordering and collapse versus flat role-then-score order.
- **T= vs B** is the full TreeSelect v0 effect (reported).
- **T=−x → T=** is the graph-expansion effect: expansion anchored on selected leaves versus on seeds (reported).
- **T= vs A** is the total product effect: what replacing the shipped selector with TreeSelect v0 would change (reported).
- **B vs A** is the cap-removal effect (reported). Together these give A → B → T=−x → T=.

Token matching makes this a selection test: it isolates *which* tokens are packed from *how many*, the confound behind ch1 and ch5. B, T=−x and T= may each pack fewer tokens than A when the floor or the pool runs out first.

## Data

The #40 frozen task set: **149 tasks** (80 held-out with edit-locus gold, 69 ContextBench with human line gold), from 51 repos. The corpora and indexes are already cached in `~/.cache/oxide-jev-eval`, and the task and gold files are committed in `docs/jev-typed-evidence-eval/tasks/`.

- This set was never used to design a selector, and #40's gate statistics stayed sealed.
- **Jev scores in that study must not be read or used.**
- #40's file-view eligibility rule (0 < gold files < view files) keeps tasks where file-level discrimination is possible. This is disclosed, not corrected.
- Debugging uses repo fixtures only. Before the first scored run there is no tuning on these 149 tasks.

## Metrics and gates

Metric definitions come from `docs/alloc-utilization-eval/scripts/score_alloc.py`: gold-line coverage, relevant tokens / used tokens, gold-symbol coverage, items, files and tokens. The scripts adapt only the input loading. Every comparison is a paired bootstrap with 10,000 resamples and seed 0, pooled over the 149 tasks, and is also reported per stratum.

| gate | rule |
|---|---|
The hierarchy gates Q1, Q2, E1 and R1 are computed on **T=−x vs B**:

| gate | rule |
|---|---|
| Q1 | T=−x − B gold-line coverage: the 95 % CI lower bound is > 0 |
| Q2 | Neither stratum (held-out, CB) has a negative point Δ for T=−x − B |
| E1 | T=−x vs B rel/used relative Δ: the CI lower bound is ≥ −5 % (the same margin as ch5) |
| R1 | T=−x vs B coverage regressions ≤ improvements, and every regression is listed with its cause |
| O1 | Median Δ context time of T= vs A ≤ 5 % of search time; peak RSS Δ ≤ 5 % |
| P1 | With the switch off, all 149 `oxide context --json` outputs are byte-identical to the frozen binary's, and two runs each of B, T=−x and T= give identical packs |

T= vs B, T=−x → T=, T= vs A and B vs A are reported with the same statistics (coverage, rel/used and per-stratum Δ, bootstrap CIs, improved/regressed counts). They do not gate.

Disposition (applies to **TreeSelect v0** only):

- **PROMISING**: every gate passes. The only next step is a new issue for a preregistered validation on fresh tasks. No production design happens before that.
- **REJECT (TreeSelect v0)**: Q1, Q2 or E1 fails. This rejects this rule, not hierarchical selection in general. If T= or T=−x beats A but T=−x does not beat B, the README states that the gain came from removing the caps (and, if T=−x → T= is positive, from re-anchoring expansion), not from hierarchical ordering.
- **INCONCLUSIVE**: only when P1 or the harness's validity fails.

## Deliverables

`docs/treeselect-eval/`, containing:

- `README.md`: answer, tables and disposition, with negative results kept;
- `research.patch`: the `src/context.rs` switch (arms B, T=−x, T=, T4096 and T=−c) plus `examples/treeselect_pack.rs`, modeled on #40's `jev_pack`;
- `scripts/`, `results/` (including the parity TSV) and `SHA256SUMS`;
- `pageindex-notes.md`.

Runs go under a capped `systemd-run` (CPU and memory limits). The only model is the shipped `arctic-embed-xs-q`.

## Out of scope

- Jev, or any model or LLM evidence.
- Routing hints / `key_items`.
- A persisted tree in the index.
- Pool depth beyond 16 seeds: a deeper pool is a separate question, and its effect would be confounded with hierarchy.
- Any change to retrieval, fusion, embeddings or the production allocator.
- Any production integration, flag or CLI/MCP surface.

