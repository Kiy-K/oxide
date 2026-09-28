# Retrieval + context failure taxonomy (research, diagnostic only)

Status: **diagnostic; production unchanged; nothing committed.** No ranking,
RRF, weight, allocator, embedding, schema, id or CLI/MCP/JSON change. Every
hook lives in a research patch (`research.patch`) applied to a detached `/tmp`
worktree, and each hook's default state reproduces production exactly (§3).
No model or LLM judge was called. Every number is **measured** unless it is
marked *inferred*. Oracles measure headroom only and are **not deployable**:
each one reads gold labels.

## 1. Baseline

- `origin/main` = **`5f9309d6c2f1320e7a258fe482c2c56ef7aaafbb`**. `src/` is
  identical to `1abb3d7`, the baseline of the allocator and Julia studies.
- Frozen configuration: RRF K=60, lexical 0.6 / semantic 0.4, depth 200,
  exact vector scan, the shipped allocator (16 seeds, 2 expansion, balanced
  ast-grep callers, dedup, floor 0.15, 5 primaries, 1 test, 2 per file,
  350-token items, 4096 budget). Balanced mode, no `--blast-radius`, no `--git`.
- Semantic channel: the shipped `native:arctic-embed-xs-q` lists committed in
  `docs/ranking-fusion-eval/results/dump-*.jsonl.gz` are injected verbatim.
  They are not recomputed, because this container cannot reach
  `huggingface.co` or `cdn.pyke.io` (the model and onnxruntime downloads).
  Semantic equality with current main is **assumed**, not proven. The
  embedding-side commits after the dumps (e.g. `5b7b8c7`, `87b97f3`) are
  refactors. Parity (§3) verifies every other stage: lexical, fusion,
  expansion, allocation and the corpora. The semantic lists are inputs, so
  parity cannot verify them.

## 2. Datasets (existing corrected sets; no gold regenerated)

| set | n | queries | gold | corpora |
|---|---:|---|---|---|
| dev (`ranking-fusion-eval/results/tasks.jsonl`) | 70 | commit subject+body | changed symbols (`oxide review` mapping) | repo HEAD at dump time |
| masked (`tasks-masked.jsonl`) | 70 | same tasks, gold-name tokens removed | same | same |
| heldout (`heldout-clean.jsonl`) | 65 | commit messages, 7 repos | changed symbols | 50 per-commit checkouts + 3 HEADs |
| cb (`cb-tasks.jsonl`) | 21 | issue text | ContextBench human gold lines, corrected path normalization | 21 base commits |

These are the four sets that have committed arctic semantic lists. The
allocator/Julia sets (#30 held-out, BCD, CA, ch5 fresh) have no committed
semantic lists, so they cannot be traced without the embedder. **Not used.**

**Corpus reconstruction** (`scripts/find_head*.py`):

- Per-commit and CB corpora are pinned by their recorded SHAs.
- HEAD corpora were not recorded. They were recovered by matching every
  dumped module span (`[1, line count]`) against git history.
  - Unique or content-equivalent commits: pylint `ba5c0c79` (dev and heldout
    share it), pytest `79833c82`, zod `2bf7b063`, requests `611c6162`, flask
    `d73fa1cd`, ripgrep `3fce3b5b`, httpx `b5addb64`.
  - Each was then confirmed by exact lexical-score and fused-order parity
    (§3). All 78 corpora are in `results/corpora.json`.

**ContextBench gold** comes from `EuniAI/ContextBench`
`data/full.parquet` on GitHub, because the HF dataset is unreachable. Its
per-instance gold-line counts equal the corrected scorer's `line.gold_size`
on **21/21** pinned instances (`docs/contextbench-scorer-fix/raw/pin21_rescore.jsonl`).

**Flags** (reported both ways; nothing was dropped silently):

- **Gold/corpus mismatch (CB).** Three instances have test-file gold with a
  spurious package prefix. The corpus has `tests/test_requests.py`, but the
  gold names `requests/tests/test_requests.py`. Same pattern for
  `pylint/tests/functional/...` and `seaborn/tests/...` (`1409977d`,
  `23963510`, `36989b6d`). That gold can never match.
  - `8d780f70` puts 30 % of its gold weight on `package.json`, which OXIDE
    does not index.
  - Together these are 0.68 lost-weight units (4.1 % of CB loss). No CB
    task is entirely invalid. §4 renormalizes without them.
- **Label uncertainty.**
  - 9 dev tasks (and their masked twins) are mechanical lint-rule sweeps
    (`Enable ruff's SIM118 …`). Their gold symbols are incidental edit sites
    no query can identify, and they account for most CANDGEN/ROUTE task
    labels on dev.
  - Commit-derived gold is also incomplete. The ranking study's judge rated
    27 % of non-gold top-5 candidates relevant, so false-positive rates below
    are upper bounds.
- **Duplicates.** Three identical-gold pairs:
  - dev `pylint-532aaac0`/`21885140` and `38bdf024`/`c1f158c6`;
  - heldout `httpx-336204f0`/`88a81c5d`.
- **Shared corpora.** pylint `ba5c0c79` serves dev, masked and heldout.
  masked = dev tasks, so the two are never pooled. "Natural" means
  dev + heldout + cb.
- The **clean subset** (sweeps and later duplicates removed, 203 instances)
  is in `results/tables.md`. Every conclusion below holds on it.

## 3. Tracing harness

`research.patch` (a worktree at `5f9309d`, built `--no-default-features`)
contains four pieces:

- **`src/retrieval/engine.rs` `taxonomy_research`:**
  - `SEM_OVERRIDE` replaces the semantic top-200 with the dumped list.
  - `CHANNELS` records the lexical/semantic top-200 lists.
  - `ORACLE_B` moves a gold id set to the front of the fused order, keeping
    the original score ladder.
- **`src/context.rs`** is the allocator study's `alloc_research` trace (seeds,
  expansion added/lost, evidence added/lost, kept pool, omissions, delivered
  windows) plus:
  - `pre_dedup`: the complete pre-allocation set with production 350-token
    windows and both test predicates;
  - `ORACLE_C`: gold reachable in one hop (`neighbors()` ∪ `callers_of`)
    from **any of the 16 current anchors** is admitted at seed score × 0.4;
  - `CANON_TEST` (#25 arm) and `ROOT_TESTS` (diagnostic arm).
- **`examples/taxonomy_dump.rs`**, per task:
  - injects the semantic list and runs the real `search` (full fused list)
    and `build_context_with`, with and without each arm;
  - records gold "bearers" (every index symbol overlapping a gold line) with
    their windows, one-hop reachability of gold from the 16 anchors, and a
    **research-only literal channel**.
  - The literal channel runs deterministic query patterns (quoted/backticked
    strings, identifier-like tokens, kebab-case message ids; ≤ 8) through
    `oxide::literal::search` (≤ 200 hits each) and maps each hit to its
    innermost symbol. Literal search is **not routed** into production
    fusion. It exists only as `search --mode literal`.
- **`scripts/analyze.py`, `report.py`** do attribution, oracles and tables.
  `scripts/run_corpus.sh` indexes with the main-checkout release binary and
  dumps.

**Parity** (`results/parity.txt`): 226/226 task-instances reproduce the
committed production dumps exactly, with the research hooks off:

- lexical top-200 keys and scores;
- the full fused order, not just the top 16;
- pack items, roles and `est_tokens`, `used_tokens`, and the complete
  omission list.

This holds after every rebuild. `git status` in the main checkout shows no
`src/` change.

### Gold units and attribution (`scripts/analyze.py`)

**Units.**

- dev, masked and heldout: each labeled gold symbol, weight 1/|gold|. It is
  delivered if its key is packed, whatever its window, or if packed windows
  cover ≥ 50 % of its lines (a packed container). Strict exact-key coverage
  is in `results/tables.md`: 0.493 / 0.249 / 0.357.
  - **Consequence:** snippet-window loss cannot be seen on these sets. 21/49
    packed gold items on dev, 12/23 on masked and 14/44 on heldout are
    truncated but count as delivered.
  - So the "snippet window" sub-reason is measurable only on CB's line gold.
  - Measuring it here would need diff-line gold, which was not generated.
- cb: each gold line, weight 1/|lines|.

An undelivered unit is blamed on the **last pipeline stage where any of its
bearers was present**. If none was ever present, it goes to the **first
stage that could have produced it**:

| stage | rule |
|---|---|
| ALLOCATION_LOSS | a bearer is in the pre-allocation set (16 seeds + expansion + callers) but the unit was not delivered. Sub-reason is the production omission reason, or "snippet window" when the bearer was packed but its 350-token window missed the gold lines. |
| FUSION_ORDER_LOSS | a bearer is in the fused list (lexical ∪ semantic top-200) at fused rank > 16, the context seed cut |
| STRUCTURAL_EXPANSION_LOSS | not in the fused list, but one hop from a current anchor |
| ROUTE_LOSS | none of the above, but the unrouted literal channel finds it |
| CANDIDATE_GENERATION_LOSS | no channel, hop or literal pattern produces it |
| EVAL_GOLD_PROBLEM | gold file not indexed, gold key absent, or line outside every symbol |

A task's primary stage is the stage holding ≥ ⅔ of its lost weight; otherwise
it is AMBIGUOUS/MULTI.

Two consequences of the bearer rules push ALLOC up slightly:

- A CB line takes the furthest stage over **all** its non-module bearers,
  including enclosing classes. A packed enclosing class therefore makes a
  line "ALLOC, snippet window" even if the method holding it was never
  generated.
- For symbol gold, only the gold key is a bearer.

Terminology note: `ranking-fusion-eval` used "route loss" for "in no
channel's top-200". Here that set splits into STRUCT + ROUTE + CANDGEN.

## 4. Failure taxonomy (share of lost gold weight; task counts in `results/tables.md`)

| set | n | coverage | EVAL | CANDGEN | ROUTE | STRUCT | FUSION | ALLOC |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| dev | 70 | 0.534 | 0.0 % | 16.4 % | 3.1 % | 1.0 % | 37.5 % | **42.0 %** |
| masked | 70 | 0.278 | 0.0 % | 21.4 % | 5.9 % | 4.0 % | **49.2 %** | 19.5 % |
| heldout | 65 | 0.416 | 0.0 % | 8.0 % | 0.0 % | 4.6 % | 41.5 % | **45.9 %** |
| cb | 21 | 0.208 | 4.1 % | 8.1 % | 0.0 % | 2.8 % | 37.1 % | **48.0 %** |
| natural (156) | | | 0.8 % | 11.2 % | 1.1 % | 2.9 % | 39.2 % | **44.8 %** |
| clean subset (203, incl. masked) | | 0.427 | 0.6 % | 12.5 % | 0.9 % | 3.6 % | 43.6 % | 38.8 % |

Primary stage by task (natural, 156):

| outcome | tasks | share |
|---|---:|---:|
| HIT | 52 | 33 % |
| ALLOC | 45 | 29 % |
| FUSION | 28 | 18 % |
| MULTI | 23 | 15 % |
| CANDGEN | 6 | 4 % |
| ROUTE | 1 | 1 % |
| STRUCT | 1 | 1 % |
| EVAL | 0 | 0 % |

**About 84 % of the useful evidence OXIDE loses on the natural sets was
already generated** (FUSION + ALLOC). It is lost choosing which of the
generated candidates reach the pack, not in generating them.

The ALLOC/FUSION split rests on a convention, so read only the combined 84 %.

- "Beyond primary cap" is, by definition, gold at fused rank 6–16, and B16
  recovers it 100 %.
- Counted as FUSION instead, FUSION becomes the largest stage on dev and
  heldout.

**Top sub-reasons** (lost-weight units, summed over tasks):

| stage | sub-reason | dev | masked | heldout | cb |
|---|---|---:|---:|---:|---:|
| FUSION | fused rank 101+, both channels ranked it > 16 | 3.80 | **15.40** | 5.17 | 2.76 |
| ALLOC | beyond primary cap (gold at fused rank 6–16) | **7.17** | 7.17 | 5.82 | 0.36 |
| CANDGEN | nothing reaches it | 5.33 | 10.80 | 3.05 | 1.20 |
| FUSION | fused 17–50, a channel had it ≤ 16 (RRF pushed it down) | 3.45 | 5.62 | 4.37 | 0.49 |
| FUSION | fused 17–50, both channels > 16 | 3.62 | 3.65 | 4.12 | 2.06 |
| ALLOC | per-file diversity cap | 3.37 | 1.20 | **6.40** | 2.24 |
| ALLOC | subsumed by overlapping symbol | 3.17 | 1.50 | 5.20 | 1.10 |
| ALLOC | snippet window (350-token cap) | 0 | 0 | 0 | **3.44** |
| ROUTE | literal finds it | 1.00 | 3.00 | 0 | 0 |
| STRUCT | within expansion scope, cut by cap / outside scope | 0.33 | 2.03 | 1.73 | 0.46 |

Anatomy of FUSION_ORDER_LOSS:

- Only **9–32 %** of its weight is gold that some channel ranked ≤ 16. That
  is the part a different fusion rule could reach.
- The rest is ranked deep by **both** channels: median lexical rank 24–52,
  median semantic rank 67–113.
- Yet **34–46 % of FUSION weight** (27–46 % of the both-deep part) is **one
  relation hop from a current seed**. The relation graph is label-free,
  request-time evidence that RRF does not use (see C-top, §7–10).

Overlap-subsumption is small and mixed:

- About half is label granularity: a whole class labeled gold (e.g.
  `Flask` 79–1499) while its edited method was delivered.
- About six held-out/dev units are real: a gold method inside a huge kept
  class whose truncated window misses it.

## 5. By query class (deterministic regex classifier, `analyze.classify`)

Natural sets, share of lost weight:

| class | n | coverage | CANDGEN | ROUTE | STRUCT | FUSION | ALLOC |
|---|---:|---:|---:|---:|---:|---:|---:|
| NL behavioral description | 50 | 0.380 | 11 % | 0 % | 4 % | 40 % | 44 % |
| mixed (long text with identifiers) | 51 | 0.472 | 7 % | 0 % | 3 % | 44 % | 45 % |
| exact identifier/symbol | 24 | 0.487 | 19 % | 8 % | 2 % | 35 % | 36 % |
| quoted literal/error text | 22 | 0.411 | 5 % | 0 % | 3 % | 35 % | 55 % |
| filename/path | 5 | 0.500 | 16 % | 0 % | 0 % | 34 % | 50 % |
| callers/impact, test discovery | 3 + 1 | — | too few to read | | | | |
| masked: NL description | 26 | 0.194 | **28 %** | 0 % | 10 % | 44 % | 18 % |

**The description-query / semantic-route-loss hypothesis is partly
confirmed.**

- The semantic channel is weak on descriptions:
  - natural NL: semantic R@16 0.26 vs lexical 0.45;
  - masked NL: semantic R@200 **0.28** vs lexical 0.61.
- Semantic misses alone rarely lose the evidence:
  - natural NL: the union of both channels reaches **R@200 0.93**, and
    only 11 % of NL lost weight is generation loss;
  - the loss shows up as FUSION (gold generated but ranked deep) and ALLOC.
- Generation loss dominates only in the artificial masked regime (28 %).
- On CB issue text, semantic ≥ lexical (R@200 0.51 vs 0.47).

## 6. Channel recall / rank (target-level; K ∈ {5, 10, 16 = seed cut, 50, 200 = channel depth})

| set | channel | R@5 | R@10 | R@16 | R@50 | R@200 | absent |
|---|---|---:|---:|---:|---:|---:|---:|
| dev | literal / lexical / semantic | .28 / .48 / .25 | .31 / .63 / .31 | .35 / .67 / .36 | .45 / .84 / .47 | .49 / .89 / .65 | 51 / 11 / 35 % |
| dev | union / fused | .56 / .34 | .70 / .52 | .73 / .66 | .84 / .81 | .92 / .89 | 8 % |
| heldout | literal / lexical / semantic | .13 / .28 / .31 | .16 / .40 / .38 | .18 / .48 / .44 | .29 / .67 / .62 | .31 / .80 / .83 | 69 / 20 / 17 % |
| heldout | union / fused | .42 / .33 | .54 / .45 | .64 / .56 | .77 / .72 | .92 / .86 | 8 % |
| cb | literal / lexical / semantic | .04 / .09 / .10 | .07 / .13 / .13 | .10 / .17 / .15 | .13 / .25 / .29 | .15 / .47 / .51 | 85 / 53 / 49 % |
| cb | union / fused | .15 / .12 | .20 / .17 | .25 / .19 | .38 / .35 | .60 / .51 | 40 % |
| masked | lexical / semantic | .30 / .06 | .35 / .07 | .40 / .12 | .50 / .20 | .65 / .35 | 35 / 65 % |

Fused recall is below the union of the two channels at every K ≤ 16. That
gap is the ordering loss. Per-class tables are in `results/tables.md`.

## 7–10. Oracle ceilings (mean coverage; all 226; paired bootstrap 95 % CI)

| oracle | what it may do | dev | masked | heldout | cb | all | Δ vs baseline |
|---|---|---:|---:|---:|---:|---:|---|
| baseline | — | .534 | .278 | .416 | .208 | .391 | — |
| **A** candidate-generation ceiling | best gold-only pack from everything generated (fused list ∪ pre-allocation set), production 350-token windows, 4096 budget, count caps ignored (greedy, so it can only under-estimate) | .910 | .819 | .932 | .577 | .857 | +.466 [.410, .523] |
| A + literal | A plus literal-channel candidates (routing) | .924 | .852 | .937 | .577 | .873 | +.016 over A |
| A index | any indexed symbol (perfect retrieval), same windows / full bodies | 1.0 / .995 | 1.0 / .995 | 1.0 / .976 | **.691 / .899** | .971 / .980 | — |
| **B** fusion ceiling | gold moved to the front of the fused order (real allocator after) | .836 | .755 | .757 | .362 | .744 | +.354 [.299, .410] |
| B16 / B50 | promote only gold already at fused rank ≤ 16 / ≤ 50 | .691 / .779 | .430 / .564 | .597 / .699 | .255 / .328 | .543 / .647 | +.152 / +.257 |
| B chan16 | promote only gold some single channel ranked ≤ 16 (any fusion rule's reach) | .729 | .524 | .639 | .271 | .597 | +.207 |
| **C** expansion ceiling | gold one hop from any of the 16 anchors admitted, **as Dependency** | .592 | .393 | .475 | .328 | .472 | +.082 [.053, .113] |
| C, primary role | same, but honoring the primary cap | .534 | .288 | .427 | .188 | .395 | **+.004 [−.006, +.017]** |
| C, top-seed score | same gold, Primary role, scored at the top seed's score (competes for the 5 slots) | .605 | .425 | .493 | .356 | .494 | **+.103 [.071, .139]**; 50 up / 6 down |
| **D** allocator ceiling | best gold-only pack from the actual pre-allocation set, same windows and budget, **count caps ignored** | .759 | .449 | .706 | .354 | .610 | +.219 [.175, .266] |
| B∘D, C∘D | oracle allocation after B / C | .910, .816 | .797, .578 | .935, .808 | .574, .535 | .851, .714 | — |

Reading the ladder:

- **Candidate generation is not the binding limit.** A (0.857) is close to
  perfect retrieval (0.971). On the natural sets, only 11 % of lost weight
  is generation loss.
- CB's lower A (0.577) is mostly **granularity**:
  - its human gold is long line ranges, so production's 350-token windows
    cap the achievable coverage (A index 0.691 with capped windows vs 0.899
    with full bodies);
  - 0.02 is gold/corpus mismatch (§2).
- **B vs D split the same headroom.**
  - Primary-cap losses are recovered 100 % by B16 on dev, heldout and masked.
  - Per-file and overlap losses are recovered 44–100 % (CB only 0–11 %,
    where snippet windows dominate).
  - "Which 5 of the 16 seeds become primaries" is one decision, and it sits
    on the fusion/allocation boundary.
- Beyond the seed cut, B50 − B16 = +0.105 and B full − B50 = +0.097. The
  latter is mostly masked description queries, gold at fused rank 101+.
- **Structural headroom depends on slot policy: +0.004 to +0.103.**
  - At expansion score (seed × 0.4) as Primary, admitted gold sorts behind
    the seeds that fill the cap: +0.004. Of 116 admitted gold items, 51 are
    dropped by the primary cap and only 9 are packed.
  - As Dependency (cap escape, the ch5 mechanism): +0.082.
  - Scored like the top seed: **+0.103 [+0.071, +0.139]**. This recovers
    23–39 % of FUSION weight (CB +0.149).
  - It is **needle-in-haystack**: the top-5 seeds have 53–71 one-hop
    neighbors per task, and gold precision among them is **0.1–0.5 %** on
    the commit sets and 2.3 % on CB (`results/tables.md`). That matches the
    ranking study's rejected flat structural bonus (`uses` at 0.4 %).
- **Routing (literal) headroom is ≤ +0.016**, and it is concentrated in
  identifier/lint-sweep queries on dev and masked. It is generous: up to
  1,600 hits with no depth limit.
- Literal "ranks" are discovery order, so only literal recall at any depth
  is meaningful. §6's literal R@5–R@50 columns should be ignored.

## 11. Concrete examples (`results/tables.md` and trace keys)

- **ALLOC, primary cap:** dev `pylint-4be9585e` ("Give every built-in checker
  an anchor…").
  - Gold `BaseChecker.get_full_documentation` is lexical rank **1**, absent
    from semantic, fused rank 6.
  - Five two-channel candidates outrank it, so it is dropped as the sixth
    primary.
- **ALLOC, per-file cap:** dev `pylint-e9f5fac5` (NaN comparison crash).
  - Gold `_is_float_nan` sits at fused rank 3 (lexical 2, semantic 5,
    literal 1).
  - Its file's two slots were already taken.
- **ALLOC, snippet window:** CB `2e76c8cd` (empty Blueprint name). The
  `Blueprint` class is packed, but the query-centred 350-token window misses
  the `__init__` lines the gold covers.
- **FUSION, RRF pushed down:** dev `flask-3d03098a`. Gold `create_app` is
  lexical 8 and literal 1, but absent from semantic, which leaves it at fused
  rank 19.
- **FUSION, both channels deep:** dev `pylint-21885140` ("Avoid crashing on
  empty len calls"). Gold `ImplicitBooleanessChecker.visit_call` is absent
  from lexical, semantic rank 168, fused 329.
- **CANDGEN:** dev `pylint-f672c5a1` ("Enable ruff's C401"). Gold
  `Run.__init__` is an incidental edit site. This is a label-uncertain sweep.
- **ROUTE:** dev/masked `pylint-342ea846` ("``Path().resolve()`` →
  ``Path.cwd()``"). Only the literal pattern `Path().resolve` finds gold
  `_config_initialization`.
- **STRUCT:** heldout `flask-c7da8c2a` ("Added python type annotation…").
  Gold `App.__init__` is an imported-definition of seed #2, cut by the
  2-item expansion cap.
- **EVAL:** CB `23963510`. The gold `requests/tests/test_requests.py` does
  not exist; the corpus has `tests/test_requests.py`.

## 12. #25 (canonical test predicate): negligible

- Over one index per repo (10 repos, 56,122 symbols), the two predicates
  disagree on 0.62 %. Most are functions literally named `test` in pylint's
  functional-test data.
- In the 226 pre-allocation sets, **10 candidates in 7 tasks** disagree:
  `test` ×6, `TestCaseFunction.runtest` ×2, `TestCaseFunction.addSubTest`,
  `_pytest`. **None is gold-bearing.**
- The research arm (canonical predicate for role assignment) changes **0 of
  226** packs, in keys or roles: Δ coverage 0.000, Δ tokens 0.
- **Recommendation: leave #25 parked or close it.** Its measured
  contribution to current failures is zero.

A related gap is *shared* by both predicates, so #25 would not touch it:
`/tests/` needs a leading slash, so symbols under a repo-root `tests/`,
`test/` or `testing/` directory are not tests unless named `test_*`.

- They fill 29 % of dev pack slots and 18 % of CB slots as primaries or
  dependencies, and only 1 of them is gold-bearing.
- A diagnostic arm treating them as tests gives Δ coverage **+0.018
  [−0.005, +0.041]**:
  - 8 tasks up and 2 down, all on the pylint-heavy dev/masked sets;
  - **0 change on heldout and CB**.
- dev/heldout gold excludes test files by construction, so that arm is biased
  in its own favour. It is not a lever.

## 13. Largest recoverable headroom

**The selection seam: which already-generated candidates (fused top-16,
then top-50) reach the ~7 pack slots.**

- It holds 84 % of natural lost weight (FUSION + ALLOC).
- Its oracle ceilings are D +0.22 and B16 +0.15 inside the 16 seeds, and
  B50 +0.26 across the top-50.
- Generation (≤ 11 %), routing (≤ +0.016) and #25 (0) are small.
- One-hop structural evidence has a comparable ceiling: +0.103 when it
  competes for primary slots. It is part of the same selection problem,
  with lower precision.

**Measured caveat: no evidence yet that this headroom is reachable from the
signals OXIDE has.**

- In the seed band (fused 6–16), gold precision is **4–12 %** in every
  channel-rank pattern (`results/tables.md`, Wilson CIs).
- "Lexical top-5, no semantic hit" is more precise only on the
  commit-message sets:
  - dev 0.125 [0.069, 0.215] vs 0.044 in both channels;
  - masked 0.102 vs 0.019.
- There is **no evidence of lift** on heldout (0.047 [0.013, 0.155], n=43,
  vs 0.057) or CB (0.067 [0.012, 0.298], n=15, vs 0.107). Those out-of-sample
  cells are small, so this is "not shown", not "shown absent".
- Only channel-rank patterns and one-hop relations (0.1–2.3 % precision)
  were tested here.
- It matches the out-of-sample rejections of K=10, lexical weight ≥ 0.7 and
  the confidence-aware reranker, and Julia's AUROC of 0.46–0.60 at this seam
  (vs 0.73–0.80 for OXIDE's order).

## 14. Is another reranker justified?

**No, not over the existing features.** Oracle B shows substantial
headroom, so here is where ordering fails and what it would need:

1. **Inside the 16 seeds (B16, +0.15):** gold at fused rank 6–16, cut by
   the 5-primary / 2-per-file caps.
   - Channel ranks give no transferable signal there (§13). RRF has no
     other signal to use.
   - A reranker would need **task-to-code relevance evidence that neither
     BM25 nor the identifier-only symbol embedding carries**. Every attempt
     to supply it so far (cross-encoder, Julia) failed.
2. **Fused 17–50, one channel ≤ 16 (≈ 9–32 % of FUSION weight):** the only
   part a different *fusion rule* can reach.
   - Its leaky ceiling, B chan16 − B16, is +0.054.
   - Its signal is identifier leakage on commit-message sets, and it did not
     transfer (K=10 rejected).
3. **Both channels deep (68–91 % of FUSION weight; masked NL dominates):**
   channel ranks cannot find these.
   - 27–46 % of them are one relation hop from a seed, so graph relations
     are the one request-time signal RRF lacks here. Their precision is
     0.1–2.3 % (§7–10).
   - The rest would need a different *representation* of candidates. The
     semantic-quality audit found `symbol_embed_text` is identifiers only.

## 15. Is a typed decision model justified?

| seam | oracle headroom (all 226) | verdict |
|---|---|---|
| query routing (literal / unrouted channels) | A+literal − A = **+0.016**; ROUTE 1.1 % of natural loss | no |
| structural expansion choice | +0.004 (expansion score) … **+0.103 [.071, .139]** (top-seed score); STRUCT only 2.9 %, but relations reach 34–46 % of FUSION weight | ceiling real, needle-in-haystack (0.1–2.3 % precision); **untested as a signal** |
| candidate reranking | B16 +0.15, B50 +0.26 (leaky) | headroom real; no signal shown |
| allocation | D +0.22 (leaky); B16/D = 0.70 dev, 0.89 masked, 0.62 heldout, 0.32 CB | largely the same seam as reranking, except CB (windows, per-file) |

- A typed decision model could only act where there is headroom: the joint
  **selection** seam (which fused-top-50 candidates and one-hop neighbors
  reach the pack).
- It is **not justified yet**. A model that ranks candidates at this seam
  (Julia) already failed, and no transferable request-time signal has been
  shown there. The structural leg is under-tested.
- Routing has nothing to gain (≤ +0.016).

## 16. Smallest next experiment

**A preregistered, zero-model separability screen at the selection seam,
run on these frozen traces.**

- Measure, only on heldout + CB (never dev or masked), within-task AUROC for
  gold among **fused ranks 1–50 ∪ one-hop neighbors of the top-5 seeds**,
  for:
  - OXIDE's fused order;
  - a feature list fixed in advance that reads no labels: relation type to
    a top-k seed and that seed's rank, query-term density in the delivered
    window, body size, kind, same-file-as-top-seed, channel agreement;
  - any future model's scores.
- Gate, fixed in advance:
  - beat fused order by a margin (e.g. +0.05 AUROC, CI excluding 0);
  - a minimum-positives rule (CB has 21 tasks).
- Only a candidate that passes starts any reranker, typed-decision,
  expansion or allocator experiment.
- The traces, harness and oracles above already make this a pure offline
  computation: no index, embedding or production change.
- If nothing passes, the headroom at this seam is closed to request-time
  evidence. The next lever is then the representation of candidates (the
  paused semantic document-text track), which targets the 68–91 %
  "both channels deep" share of FUSION loss.

## Limitations

- Semantic lists are injected, not recomputed (see §1); parity shows they
  are production-equal on these corpora.
- Four sets only (226 instances; 156 natural, 21 human-labeled). Per-class
  cells with n < 10 are shown but not interpreted.
- Oracles use gold labels, and commit-derived gold is incomplete and
  symbol-granular. Treat all ceilings as upper bounds.
- Git evidence is inapplicable: every corpus is a clean checkout with no diff.
- Blast radius is off (not default).
- The literal channel's patterns are one deterministic extraction rule, not
  a tuned router.
- Codex was not available in this environment. The independent review was
  done by a separate Claude subagent with no access to this analysis's
  reasoning (§17).

## 17. Independent review (Claude subagent standing in for Codex; read-only)

It reproduced the stage shares, recall cells, FUSION anatomy and the Oracle
B gold-first order (all 226), and found no BLOCKER.

| # | sev | finding | resolution |
|---|---|---|---|
| 1 | MAJOR | "both channels deep: no reordering can find these" contradicts the traces: 27–46 % is one hop from a seed | Claim removed. Reachability reported (§4, §14); relation features added to the §16 screen. |
| 2 | MAJOR | the C-primary null is built in: admitted at seed × 0.4, it sorts behind the cap-filling seeds (51/116 cap-dropped) | New C-top arm (top-seed score, Primary): **+0.103 [+0.071, +0.139]**. Structural verdict revised to "ceiling real, precision 0.1–2.3 %, untested as a signal". |
| 3 | MAJOR | lenient packed-key rule hides window truncation on the symbol-gold sets; strict coverage was not in the committed tables | Strict column added; limitation stated in §3. |
| 4 | MAJOR | seed-band precision came from an uncommitted script; small out-of-sample n | Now in `report.py` with Wilson CIs; reworded as "no evidence of lift". |
| 5 | MINOR | "D overlaps B16 almost entirely" overstated | Per-set B16/D ratios given (0.32–0.89). |
| 6 | MINOR | ALLOC vs FUSION depends on convention | Stated; only the combined 84 % is interpreted. |
| 7 | MINOR | bearer rules push ALLOC up slightly | Stated in §3. |
| 8 | MINOR | A/D ignore count caps | Stated in the oracle table. |
| 9 | MINOR | literal ranks are discovery order | Report shows literal R@any only; §10 note. |
| 10 | MINOR | parity cannot verify the injected semantic lists | §1 reworded: semantic equality assumed. |
| 11 | NOTE | CANDGEN is partly label uncertainty | Already disclosed (clean subset). |
| 12 | NOTE | recommendation mostly follows; structural leg under-tested, screen needs power rule | Incorporated in §15–16. |

## Reproduce

```bash
git worktree add --detach /tmp/wt 5f9309d && git -C /tmp/wt apply docs/retrieval-failure-taxonomy/research.patch
CARGO_TARGET_DIR=/tmp/tgt cargo build --release -j 4 --no-default-features --example taxonomy_dump
cargo build --release --no-default-features          # indexer (main checkout, unmodified)
# clone repos, then: scripts/build_inputs.py → inputs/*.jsonl + corpora.json
cat corp_list.txt | xargs -P 4 -I{} scripts/run_corpus.sh {}
python3 scripts/parity.py && python3 scripts/analyze.py && python3 scripts/report.py > tables.md
```

Traces (71 MB) are machine-local; `results/traces.sha256` pins them and the binaries.
