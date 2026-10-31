# Structural file outlines (Issue #44): results

**Verdict: REJECT** for both outline arms, by the rule frozen in
[preregistration.md](preregistration.md) (commit `ed74314`): no arm reaches a dev gain
of +0.05 on the primary metric. Arm A, OXIDE's current file view, stays production
behavior. The ast-grep CLI does not become a dependency, and no implementation issue
is proposed.

A secondary finding, measured but not gating: outlines **do improve where an agent
chooses to read** (+0.09 dev and +0.09–0.12 held-out gold-line recall at an unlimited
budget, higher read precision). But an outline costs more units than it buys back once
its own size is charged against the same budget, and the cost is highest exactly
where it helps most (large files).

Everything here is measured unless marked *inferred*. Arms:

- **A** is the current view.
- **B** is the `ast-grep outline` from the pinned 0.51.0, run on the captured bytes.
- **C** is a native outline rendered from indexed facts, with no parse.
- **N** shows path and length only; **F** shows the whole file; **A0** gives a file
  entity no view.

## Navigation (primary)

The agent is OpenCode with `openai/gpt-6-luna`, no tools, one turn and a fixed prompt
(see preregistration § Stage 2). It is shown candidate files (the gold files plus the
first three non-gold production search hits) with one kind of overview, and chooses up
to 8 ranges and 200 lines. Recall is over verified gold lines, macro over tasks; the
overview counts against the budget.

| recall | N | **A** | B | C | F |
|---|---|---|---|---|---|
| dev @4,096 (primary; 40 tasks × 2 reps) | 0.544 | **0.531** | 0.409 | 0.430 | 0.300 |
| dev @2,048 | 0.512 | 0.401 | 0.308 | 0.334 | 0.172 |
| dev unlimited | 0.544 | 0.587 | **0.675** | **0.674** | 0.609 |
| dev read precision | 0.149 | 0.167 | 0.251 | 0.250 | 0.304 |
| held-out @4,096 (85 tasks × 1) | 0.393 | **0.392** | 0.292 | 0.337 | – |
| held-out @2,048 | 0.386 | 0.253 | 0.162 | 0.234 | – |
| held-out unlimited | 0.393 | 0.401 | **0.490** | **0.517** | – |
| held-out read precision | 0.222 | 0.263 | 0.327 | 0.340 | – |
| median overview units (dev) | 86 | 1,516 | 2,601 | 2,192 | 17,323 |

Criteria (both arms fail; values are X − A):

| | B | C | needed |
|---|---|---|---|
| N1 dev pooled / py / ts | −0.123 / −0.169 / −0.095 | −0.102 / −0.154 / −0.070 | ≥ +0.10, none < 0 |
| N2 held-out pooled / py / ts | −0.100 / −0.217 / −0.039 | −0.055 / −0.193 / +0.017 | ≥ +0.05, none < −0.02 |
| N3 tasks up / down by > 0.10 | 3 / 15 | 5 / 15 | ≥ 3 and up ≥ 2 × down |
| N4 gain vs 2 × A noise (0.213) | −0.123 | −0.102 | > 0.213 |
| N5 @2,048 dev / held-out | −0.093 / −0.092 | −0.067 / −0.020 | ≥ −0.05 |
| V technical failures | 0 % | 0 % | ≤ 10 % |

There were 737 graded calls: dev 369 (A, N and F are the frozen pre-freeze calls, which
the post-freeze cases reproduce byte for byte) and held-out 368. No call failed, no
call used a tool, and every answer parsed. Repetition noise (mean per-task
|r1 − r2| at @4,096) was 0.106 for A, 0.050 for N, 0.027 for B and 0.023 for C.

### Per repository

The pattern follows the overview's cost, not the language.

| repository (tasks) | @4,096 A / B / C | unlimited A / B / C | overview units A / B / C |
|---|---|---|---|
| dev vscode (13) | 0.186 / 0.209 / 0.209 | 0.322 / 0.664 / 0.638 | 2,867 / 4,608 / 4,174 |
| dev requests (6) | 0.484 / 0.146 / 0.183 | 0.553 / 0.578 / 0.574 | 2,059 / 4,022 / 3,573 |
| dev code-server (2) | 0.361 / 0.167 / 0.273 | 0.361 / 0.512 / 0.495 | 1,179 / 3,494 / 2,727 |
| dev darkreader (2) | 0.350 / 0.059 / 0.013 | 0.350 / 0.059 / 0.013 | 804 / 2,265 / 1,583 |
| dev flask (1) | 0.500 / 0.000 / 0.000 | 0.500 / 1.000 / 1.000 | 926 / 4,441 / 8,293 |
| dev seaborn (1) | 0.000 / 0.000 / 0.000 | 0.051 / 0.469 / 0.425 | 4,394 / 10,985 / 13,805 |
| dev Python fixture (7) | 0.857 / 0.857 / 0.857 | 0.857 / 0.857 / 0.857 | 326 / 558 / 690 |
| dev TS fixture (8) | 1.000 / 0.787 / 0.850 | 1.000 / 0.787 / 0.850 | 245 / 423 / 441 |
| held-out MUI (43) | 0.507 / 0.509 / **0.561** | 0.515 / 0.524 / 0.565 | 896 / 1,448 / **729** |
| held-out xarray (17) | 0.264 / 0.031 / 0.059 | 0.284 / 0.539 / 0.543 | 2,450 / 10,961 / 12,383 |
| held-out pytest (12) | 0.372 / 0.179 / 0.196 | 0.372 / 0.580 / 0.629 | 965 / 4,476 / 4,646 |
| held-out vue core (13) | 0.196 / 0.023 / 0.090 | 0.200 / 0.231 / 0.220 | 1,717 / 4,016 / 3,555 |

- **Strongest outline cases.** Large files: on vscode, xarray, pytest, flask and
  seaborn, unlimited recall doubles or better. Also MUI, the only repository where C's
  outline is *cheaper* than A's view, so C beats A even on the paid metric.
- **Weakest.** The same large repositories once the outline is paid for. Xarray's
  outlines (median 11–12k units) leave no reading budget, and held-out Python @2,048
  is 0.000 for both arms. Darkreader and the TS fixture lose even unlimited: the
  outline led the agent away from the gold region.
- This matches upstream ast-grep's own benchmark (outlines help on large repositories
  and cost more on small ones; research.md), now measured for navigation decisions
  rather than cost at ceiling correctness.

## Final context at equal budgets (payload)

The frozen ContextBench scorers ran with production retrieval, routing and packing,
and only the file entity's view changed. The invariants held on every split: A.U
equals the frozen `baseline-v9`/`typescript-v6` hashes (py dev 15/15, ts dev 26/26,
py held-out 29/29, ts held-out 63/63), and entries and routing were identical across
arms. So **no difference below is a retrieval or routing effect.**

- **U (task hints only):** file entities are almost never packed with a view, so all
  arms are within ±0.04 of A.
- **H (gold files hinted):** an outline item in place of the header view loses source
  coverage. Dev, line recall X − A at 256 / 1,024 units: py B −0.208 / −0.004, C
  −0.243 / +0.013; ts B −0.091 / −0.059, C −0.095 / −0.076.
- **Attribution:** A0 frees the same budget with no outline and does as well or better
  (py dev A0.H +0.038 / +0.060). So the losses come from spending budget on structure
  instead of source, and the outline adds nothing as context.
- `pointer_recall` (a packed outline entry intersects a gold span) reaches 0.64–0.80
  at 1,024 in H. The outline points at the right code but is not the code
  (consistent with arXiv 2607.09691).
- **P fails** for both arms (best gain +0.013, worst −0.111 on dev). Held-out payload
  runs, done anyway because they produce the held-out cases, agree: B.H and C.H are
  −0.10 to −0.12 at 4,096, against A0.H −0.001 (py) and −0.081 (ts).

## Extraction

| | B (ast-grep 0.51.0) | C (native) | needed |
|---|---|---|---|
| E1 conformance recall, required entries | **0.78** (49/63) | **0.71** (45/63) | ≥ 0.90 |
| E2 ranges inside file; name in range | 100 %; 99.88 % | 100 %; 100 % | 100 %; ≥ 99 % |
| E3 deterministic (two runs) | yes | yes | yes |
| E4 failures, 980 files in 10 corpora | 0 | 0 | ≤ 1 % |

- **B misses** TypeScript overloads (`parse` at 13–14), the whole
  `abstract class Base` with its six members, a namespace member and CommonJS
  `require` imports. Both arms miss the function after `module.exports` in
  `legacy.js`.
  - On Express (CommonJS), B reports 0 import lines (C: 362) and lacks 92 functions C
    has.
  - On Preact, C has about 2,600 items and members B lacks (classes, methods,
    functions), while B has 1,591 field members C lacks.
- **C misses** what OXIDE does not index: module constants and variables, class
  fields, enum members, interface properties.
  - Six of its 18 misses are imports. That is partly the frozen matcher: one C entry
    per run of import lines can match only one required import.
  - *Sensitivity, not preregistered:* even crediting every import miss, B ≤ 0.83 and
    C ≤ 0.81, so E1 fails either way.
- **Agreement where both report:**
  - matched items with identical byte ranges: 5,418;
  - same item, different start: 572. 518 are Python, where C starts a line earlier
    (*inferred*: the decorator, which C includes and B excludes, as in the pre-freeze
    probe); 54 are TypeScript/JavaScript, on the same line (*inferred*: a leading
    keyword such as `export`);
  - import lines: identical on Python and TS sources except multi-line imports, which
    B drops (requests 273 vs 306, seaborn 806 vs 998).

## Operations

| | B | C | limit |
|---|---|---|---|
| per-file outline p95 (worst corpus) | 37.6 ms | 24.5 ms | 100 / 50 ms |
| `build_context` median vs A | 1.034 × | 1.087 × | ≤ 1.25 × |
| ast-grep peak RSS | 171 MB | – | ≤ 256 MB |
| determinism | yes | yes | |
| platforms | wheels for Linux x86_64/aarch64, macOS x86_64/universal2, Windows (PyPI metadata; runtime checked on Linux only) | no new dependency | |

- **Rendered size**, as units summed over the corpora:
  - C is smaller than B on TypeScript (code-server 11.9k vs 15.2k, darkreader 33.6k vs
    50.5k).
  - C is larger on JavaScript (Express 8.5k vs 3.9k, because B misses so much) and on
    Python with many methods.
  - Both are far smaller than whole files (5–15 %).
  - Both are sometimes larger than A's view (flask, seaborn) and sometimes much
    smaller (requests 13.5k vs 220k: A's single-run fallback shows whole modules).
- **No new index facts and no reparse** for C, which reads only `contains`, signatures
  and `imports` evidence. B's outlines are cached by digest; it spawns one process per
  file.

## Limitations

- **One model and one agent setup.** OpenCode with `openai/gpt-6-luna`, a no-tool
  single turn. It is not a full agent loop, where an outline could be requested on
  demand rather than shown up front. Cost was not measured (OpenCode reports none for
  this provider).
- **Agent switches before the freeze** (Claude Sonnet, then OpenCode's free tier) are
  disclosed in the preregistration. Their runs are kept in the data directory and
  unused.
- **Small dev split** (40 tasks with gold), half of them fixtures; four dev
  repositories have 1–2 tasks. Held-out is larger (85) and agrees on the primary
  metric.
- **JavaScript/JSX** appears only in extraction (Preact, Express and the fixture); no
  JS task has verified gold.
- **Linux only** at runtime; the other platforms are checked from PyPI wheel metadata
  only.
- **The verdict holds even ignoring E1.** Navigation alone fails N1–N5.

## Post-freeze changes (all to the analysis, none to criteria or arms)

- **`analyze.py`:** a task without verified gold lines (vscode-135805) has a `None`
  metric. The analysis summed it and crashed. It now leaves such tasks out, as the
  preregistration and the pre-freeze A values already did.
- **No other script, prompt, renderer, case or threshold changed after `ed74314`.**
  The regenerated dev cases hash-match `results/prefreeze/cases.sha256`.

## Recommendation

- **Keep A.** Do not add an outline to the payload or replace the file view with one,
  and keep ast-grep out of production and the `oxide_search` contract.
- **Kept as negative evidence:** this README, `results/report.json` (every criterion
  and payload delta), `results/nav.json` (every graded call) and
  `results/extract/` (conformance and corpus reports).
- **No implementation issue is proposed.** The unpaid decision-quality gain is a
  *different* hypothesis: an outline the agent can request for one large file, or one
  that fits a small budget, rather than one shown for every candidate. Testing it would
  need its own preregistration with an agent loop. If that is ever done, C is the
  candidate: it carries no dependency, is cheaper on TypeScript, is the only arm that
  beat A anywhere (MUI), and its gaps are fields and constants that OXIDE could index.
  *Inferred, not tested here.*

## Data and reproduction

The raw runs are in `~/Projects/oxide-eval-data/oxide-outline-eval`. That covers every
OpenCode call record (`nav-runs/`), the context runs and the case files (hashes in
`results/prefreeze/cases.sha256` and `results/heldout-cases.sha256`). Commands are in
preregistration § Reproduce; `scripts/analyze.py RESULTS OUT` recomputes
`results/report.json`.
