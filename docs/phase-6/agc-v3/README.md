# AGC v3: tool adoption vs tool utility on structural tasks: PROMOTE G (preregistered), with caveats

**Preregistered decision: PROMOTE G.** The recommendation is to propose an ADR for a direct
`oxide_context` MCP tool with guidance by question type. No ADR is written and no production
code changes in this study. AGC v1 (7eca108) and v2 (5cf699d) stay REJECT; they asked
different questions on tasks that grep/read already solved.

- **Freeze:** the preregistration ([preregistration.md](preregistration.md)) was frozen
  before any scored run; hashes in [results/freeze.txt](results/freeze.txt), re-checked
  OK after the runs.
- **Runs:** 84 scored runs: 7 rule-selected tasks × 4 arms × 3 reps, OpenCode 2.1.4 with
  `openai/gpt-6-luna`. There were 0 technical failures and 0 runs missing the tool schema.
  F's first call was the tool in 21 of 21 runs.
- **Stage 1** (28 runs) met both the adoption and the utility signal, so the study
  continued ([results/stage1.json](results/stage1.json)).

All numbers are measured; statements marked *inferred* are interpretation.

## Results (3 reps; B = grep/read, D = direct tool, G = D + guidance, F = forced-use diagnostic)

| | B | D | G | F (diagnostic) |
| --- | --- | --- | --- | --- |
| Success (F1 ≥ 0.8, 0 false claims) | 0.571 | **0.762** | 0.667 | **0.857** |
| Macro F1 | 0.841 | 0.894 | 0.854 | 0.932 |
| False claims / run | 2.05 | 1.38 | 1.90 | **0.67** |
| CPS ratio to B (95% CI over tasks) | 1 | **0.58 (0.36–0.95)** | 0.78 (0.48–1.23) | 0.58 (0.30–0.94) |
| Tokens / seconds / calls ratio | 1 / 1 / 1 | 0.76 / 0.86 / 1.02 | 0.97 / 0.94 / 1.08 | 0.79 / 0.81 / 1.01 |
| grep / read calls per run | 3.33 / 3.67 | 1.86 / 3.29 | 0.86 / 3.05 | 1.05 / 3.14 |
| Redundant reads per run | 1.10 | 1.10 | 0.86 | 0.71 |
| Organic OXIDE use (runs) | – | **0.67** (14/21) | **0.95** (20/21) | forced (not adoption) |
| OXIDE calls per run | – | 1.76 | 3.33 | 3.00 |

| By type | B | D | G | F |
| --- | --- | --- | --- | --- |
| Dispatch (D1–D3): success / false per run / organic use | 0.44 / 3.22 / – | 0.67 / 2.22 / 0.44 | 0.56 / 3.00 / 0.89 | 0.67 / 1.56 / – |
| Impact (I1–I4): success / false per run / organic use | 0.67 / 1.17 / – | 0.83 / 0.75 / 0.83 | 0.75 / 1.08 / 1.00 | 1.00 / 0.00 / – |

**Per-task successes, out of 3:**

| | B | D | G | F |
| --- | --- | --- | --- | --- |
| D1 | 1 | 0 | 0 | 1 |
| D2 | 1 | 3 | 3 | 2 |
| D3 | 2 | 3 | 2 | 3 |
| I1 | 0 | 1 | 0 | 3 |
| I2 | 2 | 3 | 3 | 3 |
| I3 | 3 | 3 | 3 | 3 |
| I4 | 3 | 3 | 3 | 3 |

Full details are in [results/report.json](results/report.json) and
[results/runs.json](results/runs.json).

**Criteria** (preregistration § Decision rule): U (utility) ✓, A (adoption) ✓, P (product
gate) ✓, with no premium needed.
- **U:** F's success is 0.857 vs B's 0.571 (+0.29), with false claims 0.67 vs 2.05.
- **A:** G's organic use is 0.95, which is ≥ 0.50 and ≥ D's rate (0.67) + 0.20.
- **P:** G's success is 0.667 (≥ B − 0.05), F1 0.854 (≥ B − 0.03) and false claims 1.90
  (≤ B's 2.05). Its tokens ratio is 0.97, seconds ratio 0.94 and CPS ratio 0.78 (≤ 1.00).

## Exposure vs invocation

| | D | G | F |
| --- | --- | --- | --- |
| First-step tokens added over B (tool schema 287, plus prompt text) | 287 | 450 | 481 |
| Estimated exposure cost per run (tokens × (1 + 0.1·(steps−1))) | 406 | 675 | 687 |
| Cost vs B, runs that never called the tool (n) | 0.94 (7) | 1.33 (1) | – |
| Cost vs B, runs that called it (n) | 0.75 (14) | 0.94 (20) | 0.88 (21) |
| Tool output per invoking run (~chars/4 tokens), server time | 1 010, 0.41 s | 1 365, 0.46 s | 1 291, 0.43 s |

- **Exposure is small.** B's mean run costs about 17.9k cost units, so exposure is about
  2–4% of a run.
- **Invocation is net negative.** Runs that call the tool cost less than B: grep calls
  drop by 44–74%.
- **Infrastructure:**
  - Per-session startup, outside run latency: a median of 4.57 s in the server arms vs
    0.55 s in B, of which 2 s is the harness's settle (below).
  - Indexing is one-time: 1.7 s for xarray and 1.1 s for seaborn, 49 MB in total.

## What the evidence says

1. **These tasks are harder for grep/read.** B succeeded in 57% of runs and made 2.05 false
   claims per run, against 94% and 0.03 in v2. Most of B's errors are same-named methods
   it couldn't tell apart (the `DatasetGroupBy`/`BarBase`/`Dots` traps), callers one hop
   too far, and citations of the wrong line.
2. **The graph helps when it is used (U).** F was the best arm on every measure:
   - success +0.29 over B, with false claims cut by 67%;
   - 0.58× B's CPS and 0.81× its latency;
   - fewer redundant reads.
3. **Adoption depends on the task, not only on guidance.** With no guidance at all, D used
   the tool in 67% of runs here, against 21% in v2. *Inferred:* agents reach for the graph
   when the question is visibly relational and grep returns too many matches. Guidance
   raised use to 95%.
4. **Guidance raised adoption but not outcomes.** G did worse than D on success (0.667 vs
   0.762), false claims (1.90 vs 1.38) and CPS (0.78 vs 0.58). G's I1 runs followed the
   guidance's `depth=2` and then kept walking (to `dataset_repr` and the `__repr__`
   methods), reporting third-hop callers as second-hop: 0/3 successes, against F's 3/3.
   - *Inferred:* the guidance's "depth=2, do not grep again" pushed the agent to trust
     and extend graph walks instead of checking hop counts.
   - So the guidance text, not the tool, is the weak part of G.
5. **D1 is confounded by its wording.** "A call that can execute `Variable.reduce`"
   admits transitive callers, and every arm's D1 "false claims" are mostly that.
   [scripts/sensitivity.py](scripts/sensitivity.py) re-grades D1 with those claims as
   neutral; this is descriptive, not preregistered.

| Success / false claims per run / CPS ratio | B | D | G | F |
| --- | --- | --- | --- | --- |
| As preregistered | 0.571 / 2.05 / 1 | 0.762 / 1.38 / 0.58 | 0.667 / 1.90 / 0.78 | 0.857 / 0.67 / 0.58 |
| D1 graded leniently | 0.619 / 1.14 / 1 | 0.810 / 0.52 / 0.59 | 0.810 / 0.67 / 0.69 | 0.905 / 0.10 / 0.60 |
| D1 dropped (6 tasks) | 0.611 / 1.28 / 1 | 0.889 / 0.50 / 0.50 | 0.778 / 0.78 / 0.70 | 0.944 / 0.06 / 0.51 |

Every OXIDE arm beats B in all three views. The ordering F ≥ D ≥ G holds in each.

## How strong the PROMOTE is

The preregistered rule says PROMOTE G. The evidence for an agent-facing direct tool is
stronger than the evidence for G's particular guidance.

**Fragile:**
- G's CPS interval crosses 1 (0.48–1.23).
- G passes F (false claims ≤ B) only narrowly: 1.90 vs 2.05.

**Robust:**
- D (no guidance) beats B with a CPS interval entirely below 1.
- F, which shows what the tool is worth when used, beats B on every measure.

**Recommendation** (*inferred* from the above):
- Propose an ADR for a direct, read-only `oxide_context` MCP tool (one tool; callers and
  callees with residuals; tiered outlines).
- Ship it with light guidance and no fixed `depth=2`. G's text should be revised and
  re-tested before it becomes product copy.
- Code mode stays excluded: v2 measured it as a ×1.4–1.5 CPS tax.

## Threats and limitations

- **Small sample:** 7 tasks, two Python repositories, one client and one model. Two
  seaborn impact tasks (I3, I4) were easy and saturated every arm.
- **The truth leans toward the graph's view.** It counts direct dispatch and hops by call
  edge, which is close to what a call graph computes. It was written by hand, from source,
  without consulting OXIDE, but the construct aligns with the graph (*inferred*).
- **D1's wording**, above.
- **Dispatch truth decides receivers by hand.** Where a receiver's type isn't evident in
  its function, the call is neutral or excluded. The candidate rule was revised once before
  the freeze for this reason (preregistration § Tasks).
- **Cost is a token-price proxy:** the provider reports $0.
- **A harness race, fixed and checked.** In one dev run, OpenCode sent step 1 without the
  tool even after MCP reported `connected`. The harness now waits a 2 s settle, and the
  validity check confirmed the schema in all 63 scored server-arm runs. v2's 36 Ad runs all
  carried it.

## Reproduce

Scripts are in `scripts/`; data is in `~/Projects/oxide-eval-data/oxide-agc3-eval`.

```
python select_tasks.py          # results/selection.json (seeded candidate pools)
python trial.py check           # truth keys resolve; checkouts digested
python trial.py run REP 2       # one repetition under systemd-run caps
python trial.py verify && python trial.py grade
python3 analyze.py invalid      # runs whose first step lacked the tool schema (none)
python3 analyze.py stage1       # results/stage1.json: CONTINUE
python3 analyze.py              # results/report.json: the decision
python3 sensitivity.py          # D1 re-graded leniently (descriptive)
```
