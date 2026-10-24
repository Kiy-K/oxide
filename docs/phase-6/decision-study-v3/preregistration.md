# Decision Study v3 preregistration: independent quality confirmation

Frozen 2026-10-24, before any v3 judge call and before OXIDE has searched, routed, packed
or scored any v3 task. The freeze is the commit that adds this file, `scripts/`,
`tasks.json`, `gold-audit.json` and `separation.json` (parent `066291f`). `src/` is not
changed and no judge is integrated, whatever the verdict. No ADR is drafted unless the
gate below passes.

## What stays fixed

- Decision Study v1 is **REJECT**, Decision Study v2 is **INCONCLUSIVE** (quality criteria
  passed on held-out, H7 failed on HTTP 529s), and the JEV reliability study is **PASS
  under immediate fallback**. None of these is reopened or reinterpreted here.
- v2's held-out split (29 xarray/pytest tasks) is consumed. It is not resent, rescored
  or used as evidence. No baseline corpus (dev or held-out) is sent to JEV in this study.
- **The policy under test is frozen**, exactly v2's: production's heuristic admits, JEV
  orders the admitted set, the packer is unchanged, and any failure falls back to
  production for the whole request. Nothing about the selector, floor, heuristic,
  capsule, renderer, questions, model or mapping is tuned, retrained or extended.

## Hypothesis

On Python tasks and repositories never used before, ordering production's admitted
candidates by JEV raises packed gold-line recall at matched budgets **end to end** (every
task, failed requests included as fallbacks), beyond a random order of the same admitted
set, without per-task harm, and reproducibly.

## Disclosure (known before this freeze)

- Everything in Decision Studies v1/v2 and the reliability study, including v2's
  held-out result (Δ@4096 +0.125 end to end, +0.191 on answered requests).
- How the v3 set was built (`separation.json`): candidate counts, the prior-artifact
  scan, per-repository `*.py` counts at each base commit (git trees only), and the gold
  audit classes (span integrity against the corpus, no OXIDE involved).
- One index-only timing run on `ansible__ansible@9b0d2decb24b` (1,550 files, 4.8 s,
  281 MB RSS), to set the cost rule. It ran no query and touched no task or gold.
- This code's validity run on the baseline **dev** split with v2's recorded dev
  judgments (README § Validity). Dev is spent data.
- `call.py selfcheck` on localhost (README § Deadline).
- No v3 request, judgment or arm outcome exists. The heuristic's recall on v3 is unknown.

## Evaluation set (`scripts/taskset.py`)

Source: ContextBench `full.parquet` (sha256 `2f56535b…`, the baseline's pinned file),
`language == python`. Rule, in order:

1. The repository is not one of the baseline's five (`contextbench.ELIGIBLE`: requests,
   flask, seaborn, xarray, pytest), which covers every task of Decision Studies v1/v2,
   the reliability study and every Python-engine benchmark.
2. Neither of the task's ids (`instance_id`, `original_inst_id`) is named in any prior
   artifact: a substring scan of `*.json|jsonl|md|txt|csv|log|py` under
   `~/Projects/oxide-eval-data`, `docs/`, `tests/`, `scripts/` and `eval-agent/` (checkouts
   and stores pruned).
3. Per repository, in `sha256("ds3:" + instance_id)` order, accept a task while the
   repository has fewer than **8**, if its corpus (tracked `*.py` at `base_commit`) has
   at most **3,000** files (cost rule) and at least one gold span verifies with a code
   line under the baseline's audit rule (`contextbench.classify`).

Result: **52 tasks over 52 corpora** (one base commit each) from 8 repositories:
ansible 8, openlibrary 8, qutebrowser 8 (SWE-bench Pro); transformers 8, keras 8,
langchain 8, yt-dlp 3, AutoGPT 1 (SWE-PolyBench). Of 475 candidate rows, 303 are named in
prior artifacts (every SWE-bench-Verified repository's tasks appear in legacy
agent-navigation grading files; 27 more were used by legacy Rust-era studies), leaving
172; 4 corpora failed the cost rule. Gold audit: 637 exact, 15 contained, 2 path spans
verified; 54 unresolved, 28 drift, 13 range, 1 revision not scored. 40 of 52 problem
statements exceed the 1,024-byte query clip (median 1,509 bytes), so the judge sees the
same clipped task text production would.

Query: the problem statement, verbatim (the capsule clips it to 1,024 bytes, as
production does). Gold: ContextBench's human `gold_context` spans; only verified spans
are scored, exactly as the baseline.

**Separation (checked, `separation.json`):** repositories, task ids and corpus names
are disjoint from the baseline; no task id appears in any prior artifact; no `*.py` file of at least 512 bytes in any
v3 corpus is byte-identical to one in a baseline corpus (0 shared).
*Limit*: some v3 repositories had **other** tasks in legacy Rust-era studies, so each
repository is new to the Python engine and to every decision study, but not to the
project as a whole.

**Gold isolation:** the capsule (`oxide-capsule-v1`) carries no heuristic value, selection
outcome, gold or oracle field (SPEC). Requests are written with `--requests-only`, which
computes no arm. `score.py` asserts for every request that it has only `task`, `corpus`,
`query` and canonical capsules, that every capsule has exactly the capsule fields, that
the query is the problem statement, and that the body sent contains no gold-content
digest, no benchmark instance id and no task id. `call.py` builds each body from the
request file alone. Raw responses stay outside the repository.

## Judge configuration

Unchanged from v2's primary arm: TypeSafe JEV `POST https://api.typesafe.ai/v1/systemone`,
model pinned `jev-1.14.0` (the reply must name it), renderer `ds1-compact-v1` with rank
(task once; per capsule a header line and the excerpt bounded to 1,024 bytes), capsules
in digest order, one call per request, one `noul` question per capsule with v1's
`QUESTION`/`CRITERIA` (`render.QUESTIONS_DIGEST`), JEV default context limits, no
retries, concurrency 1. Bodies are built by v1's `adapter.py`, as in v2.

**Changed: the deadline is enforced.** v2 killed calls at 60 s and only reported latency
against production's `DEADLINE`. Here `call.py` kills the child process at **2.0 s**
(`capsule.DEADLINE`, unchanged), measured from before the child starts, so a late reply
is never read; a reply measured over 2.0 s is discarded as `late`. Manifest digest
`sha256:d9ae934c9a6a3650a6f2ca6ef3f3ab919103cc7e9788951a7b8699c354407b1d` (v1's manifest
with `deadline_s: 2.0`).

**Mapping (v2's `harness.hybrid`, unchanged).** With `h = packing.value` and `F = 0.4`,
the admitted set A is every candidate `pack` asks about with `h ≥ F`. If every member of
A has a sendable capsule and a valid judgment `p ∈ [0, 1]`, each gets `F + 0.59 p` and
every other candidate abstains; otherwise the whole request keeps the heuristic. Then
`pack` as committed (hinted first, value descending, pool-position ties, greedy first
fit, 24 items).

**Fallback (immediate, request-level, no retry).** A request falls back to production
when it is not sent (cost cap); gets any non-200 status (including 529); is killed at
the deadline; is late; names another model; is unparsable; answers fewer questions than
it has capsules; or leaves an admitted candidate unjudged or unsendable. A fallback is
production, byte for byte, by construction; `report.py` asserts it for every such task.

## Arms (all pack the same pools at 256 / 1,024 / 4,096 with the unchanged packer)

| Arm | Role |
| --- | --- |
| `heuristic` | production (deterministic-heuristic control); asserted equal to the scorer on every task and budget |
| `random~order_s0…4` | control: the admitted set in a seeded random order, mean of 5 |
| `oracle_order`, `oracle_pick` | diagnostic ceilings only, never gated |
| `compact~order` | **primary**: one fresh call per task |
| `compact-repeat~order` | reproducibility: the primary's request files, checked by sha256, sent again after the primary arm completes |

Execution, once each, in this order: `score.py --requests-only` → `call.py estimate` →
**user authorization** → primary arm → repeat arm → `score.py` with both judgment files
→ `report.py`. A failed request is never resent, and the set is never run a second time.

## Measurements (`scripts/report.py`)

**End to end** (all tasks with verified gold; failed requests are fallbacks):

- `line_recall` @256 / 1,024 / 4,096 per arm; Δ vs `heuristic`; utilization (packed
  units ÷ budget). Matched budget: identical pools, scopes, declarations and budgets for
  every arm, and `used ≤ budget` asserted.
- Per task: Δ at every budget; wins (> +0.005), losses (< −0.005), ties; every
  task-budget regression of the primary and the repeat, listed.
- Uncertainty: paired percentile bootstrap 95% CI of the mean Δ@4096 over tasks (10,000
  resamples, seed 0); repository-cluster bootstrap CI and leave-one-repository-out
  Δ@4096 (reported, not gated).
- Within-task AUROC on the admitted set.

**Conditional on an answered request** (reported, not gated): the same Δ and CI over the
tasks whose request answered, the count that answered but still fell back (an
unsendable admitted candidate), and the heuristic's recall on that subset, so any
selection effect is visible. Both views are always printed; no task is excluded from
the end-to-end view.

**Operations**: per arm, status counts (200, 529, other HTTP, killed, late, wrong model,
unparsed, incomplete, not sent); failure rate with a Wilson 95% interval; latency
p50/p95/max over sent and over answered requests; billed input tokens per request;
cost; billed tokens per request per +1 recall point.

**Reproducibility**: request digests identical between the requests-only run, both
arms' sent bytes and the scored run; the repeat arm's gain; the median within-task
Spearman (admitted set) of repeat vs primary and their ΔΔ per budget, over the tasks
both answered.

## Acceptance criteria

All thresholds are fixed here and in `report.py`.

- **Q1 recall**: end-to-end primary Δ@4096 ≥ +0.020, bootstrap CI lower bound > 0.
- **Q2 no harm**: no budget's end-to-end primary macro below heuristic by more than 0.005.
- **Q3 random order**: primary @4096 ≥ random-order mean + 0.020.
- **Q4 regressions**: tasks with Δ@4096 < −0.005 are ≤ 20% of tasks and fewer than wins.
- **Q5 reproducibility**: repeat end-to-end Δ@4096 ≥ +0.020, and median Spearman ≥ 0.90.
- **O1 failures**: primary requests without a valid answer by 2.0 s ≤ 10% of tasks
  (v2's H7 and the reliability study's O1).
- **O2 replies**: no wrong-model, unparsable or incomplete reply in either arm.
- **O3 spend**: ledger ≤ the cap and no request unsent for cost.

**Verdict.** **PASS**: Q1–Q5 and O1–O3. **REJECT**: end-to-end Δ@4096 ≤ 0, or Q3 or Q4
fails, or (with ≥ 10 answered tasks) the conditional Δ@4096 ≤ 0. **INCONCLUSIVE**:
anything else, including an operational failure with quality intact.

**Validity** is asserted before any number is printed: the heuristic arm equals the
scorer; every fallback packs production exactly; request digests match; the leak check
holds; `used ≤ budget`. A validity failure stops the report. Only harness code may then
be fixed and the recorded judgments re-scored, with the fix disclosed; no request is
resent and the policy never changes.

Latency: the 2.0 s deadline is a hard limit by construction (anything slower is a
failure under O1); answered p95 is reported. Changing `DEADLINE` is an ADR decision.

## Spending

Hard cap **$0.20** of billed JEV spend for the whole study (both arms), in a ledger
outside the repository. `call.py` refuses an arm whose tiktoken estimate would exceed the
remaining cap, sends a request only if the ledger plus its estimate stays within the cap,
and charges a request that reports no billed tokens its estimate. Expected (*inferred*
from v2's ~15k billed tokens a request at $0.042 per million): ~$0.0006 a request, about
$0.03 an arm and $0.07 for both (at v2's largest request, 24.7k tokens, at most $0.11). No call is made until the user authorizes the corpora list printed by
`call.py estimate` and this cap.

## Afterwards

- **PASS**: the gate allows proposing an ADR for an optional judge adapter with this
  mapping, as a separate step. Nothing is integrated here.
- **REJECT** or **INCONCLUSIVE**: report it with costs and operational limits.
- The v3 set is consumed either way and is never rerun as fresh evidence.
