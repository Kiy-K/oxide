# Decision Study v2 preregistration: heuristic admission, JEV order

Frozen 2026-10-24, before any v2 model call. The freeze is the commit that adds this file
and `scripts/` (parent `8d10ca2`, which records Decision Study v1). Code under test is
unchanged from `fef45e5` (`oxide-select-greedy-v4`, `oxide-capsule-v1`, `DecisionProvider`
seam); nothing in `src/` changes, and no judge is integrated into production whatever the
verdict.

## Hypothesis

Keeping production's heuristic admission and using JEV only to **order the admitted
candidates** raises packed gold-line recall at matched budgets on Python held-out,
beyond a random order of the same admitted set, without rank copying.

The selector, the floor, the heuristic, the capsule, the renderer and JEV are not
redesigned, retrained or extended with ranking features.

## Disclosure

Everything below was seen before this freeze, on Python **dev** only:

- Decision Study v1 ([../decision-study-v1](../decision-study-v1/README.md)), including its
  post-hoc `~order` diagnostic: this exact mapping reached `line_recall@4096` 0.783 vs
  heuristic 0.659 (`oracle_order` 0.792, random order 0.583) on v1's recorded judgments.
  **The mapping was chosen after seeing dev outcomes**, so dev cannot confirm it; only
  held-out can.
- The stability investigation (README § Stability), on v1's recorded judgments: ties
  never change recall; measured judgment noise moves 0.2–1.2 dev tasks a run by up to
  0.30 each, giving a macro sd@4096 of 0.008 and a 20% chance of exceeding v1's ±0.010
  tolerance per control. Admitted-set Spearman on v1 data: repeat 0.986, reversed 0.931.
- Held-out: only its size (29 tasks: 17 xarray, 12 pytest commits). No held-out
  request, judgment or v2 arm outcome has been produced.

## Mapping (the policy under test)

Per request (one task, one budget-independent judgment call), with `h` = `packing.value`
and `F` = `packing.INCLUDE_FLOOR` (0.4):

1. **Admitted set** A: the candidates `pack` asks a judge about (has a source, not a
   container) with `h ≥ F`. Exactly production's admission.
2. If every candidate in A has a sendable capsule and a valid JEV judgment `p`
   (a number in [0, 1], not a bool), each `c ∈ A` gets value `F + 0.59 × p(c)` (in
   [0.40, 0.99], so all of A stays admitted) and every other candidate abstains (keeps
   `h < F`, so stays excluded).
3. **Fallback**: otherwise (request failed, killed, wrong model, unparsable, or any
   admitted candidate unsendable or unjudged) the whole request keeps the heuristic,
   i.e. production exactly. There is no per-candidate mixing of scales.
4. Everything else is `pack` as committed: hinted candidates first, then value
   descending, ties by pool position; greedy first fit, item cap 24, same budgets.

Implementation: `scripts/harness.py` `hybrid`.

## Model configuration

As v1's gate renderer, unchanged: TypeSafe JEV `POST https://api.typesafe.ai/v1/systemone`,
model pinned `jev-1.14.0` (reply must name it), manifest digest
`sha256:fb74d125ca2dbe7d20ac961038eb2233d136bad816896fd28da25a9f446cf51b`; renderer
`ds1-compact-v1` (task once; per capsule a header line with rank and the excerpt bounded
to 1,024 bytes); one call per request, one `noul` question per candidate with v1's
`QUESTION`/`CRITERIA`; capsules in digest order; JEV default context limits; child process
killed at 60 s; no retries. Calls are made by `scripts/judge.py` over v1's
`adapter.py`. Latency is reported against production's 2.0 s `DEADLINE`, which is not
changed.

## Arms (every arm packs the same pools at 256 / 1,024 / 4,096)

| Arm | Role |
| --- | --- |
| `heuristic` | production; must reproduce the scorer on every task and budget (asserted) |
| `oracle_order`, `oracle_pick` | diagnostic ceilings only, never a gate |
| `random~order_s0…4` | control: the admitted set in a seeded random order, mean of 5 |
| `compact~order` | **primary**: fresh calls, digest order, with rank |
| `compact-norank~order` | control: rank and score removed from the header (`ds1-compact-v1-norank`) |
| `compact-repeat~order` | control: the primary's requests sent again, identical |
| `compact-reversed~order` | control: presentation order reversed (position bias) |

All four model arms are fresh v2 calls, on dev and (if opened) on held-out. v1's recorded
judgments may be replayed for comparison but are never gated.

## Matched budget

Each arm packs the identical pool, scopes and declarations at the identical budget with
the unchanged packer, so `used ≤ budget` for every arm (asserted). Because admission is
production's, every model arm and the random-order control pack from the same admitted
set as production: they differ only in order.

## Measurements (macro over tasks with verified gold)

- `line_recall` @256 / 1,024 / 4,096 per arm, and Δ vs `heuristic`.
- Per task: Δ@256/1,024/4,096 of the primary vs heuristic; wins (> +0.005), losses
  (< −0.005), ties; paired percentile bootstrap 95% CI of the mean Δ@4096 over tasks
  (10,000 resamples, `random.Random(0)`).
- Budget utilization: mean packed units ÷ budget, per arm and budget.
- Ordering quality: within-task AUROC on the admitted set.
- Repeatability: median within-task Spearman (admitted set) of the primary vs repeat and
  vs reversed; their ΔΔ per budget.
- Operations: failed and fallback requests; latency median, p95, max; billed input
  tokens per request and total cost (published $0.042 per million input tokens).
- Cost per useful improvement: billed tokens per request ÷ (100 × Δ@4096).

## Cost limits

Hard cap **$0.30** of billed JEV spend for the whole study (dev and held-out), kept in a
ledger outside the repository. `judge.py` refuses to start an arm whose tiktoken estimate
would exceed the remaining cap and stops sending at the cap; unsent requests are failures.
Expected: ~$0.04 dev (60 requests), ~$0.10 held-out (116 requests). Every corpus sent
needs explicit user authorization; raw requests and responses stay outside the repository.

## Dev gate (fresh calls; all must pass)

- **D1 recall**: primary Δ@4096 ≥ +0.030, Δ@1,024 ≥ 0, and no budget below heuristic by
  more than 0.005.
- **D2 random order**: primary @4096 ≥ random-order mean + 0.030.
- **D3 rank**: the no-rank control keeps ≥ 50% of the primary's Δ@4096.
- **D4 regressions**: at most 1 task with Δ@4096 < −0.005.
- **D5 reproducibility**: repeat and reversed each have Δ@4096 vs heuristic ≥ +0.030,
  |ΔΔ| vs the primary ≤ 0.030 at 1,024 and at 4,096, and median within-task Spearman
  ≥ 0.90. *Changed from v1's ±0.010*: one task flip moves the 15-task macro by about
  0.02 (stability investigation), so ±0.010 tests whether any task flips, not whether
  the gain reproduces; ±0.030 is about 3.7 simulated sd.
- **D6 operational**: at most 1 failed request of 15 in the primary.

If D1–D4 fail: **REJECT**. If only D5 or D6 fail: **INCONCLUSIVE**. Either way held-out
stays sealed. `harness.py --split heldout` refuses unless the dev gate output contains
`DEV GATE: PASS` and this file's sha256.

## Held-out confirmation (once, only after the dev gate passes)

Same arms, same code, fresh calls, no change of any kind after the dev gate.

- **H1** primary Δ@4096 ≥ +0.020 and bootstrap CI lower bound > 0.
- **H2** no budget below heuristic by more than 0.005.
- **H3** primary @4096 ≥ random-order mean + 0.020.
- **H4** the no-rank control keeps ≥ 50% of the gain.
- **H5** losing tasks ≤ 20% of tasks and fewer than winning tasks.
- **H6** repeat and reversed each have Δ@4096 ≥ +0.020.
- **H7** failed requests ≤ 10% of tasks.

**PROMOTE** (to an ADR proposal for an optional judge adapter with this mapping; not
production integration): H1–H7 pass. **REJECT**: Δ@4096 < 0 or H5 fails.
**INCONCLUSIVE**: otherwise. Deployability is reported separately: p95 latency ≤ 2.0 s and
the cost per useful improvement; changing `DEADLINE` is an ADR decision.

Oracle ceilings are diagnostic references only and enter no gate.
