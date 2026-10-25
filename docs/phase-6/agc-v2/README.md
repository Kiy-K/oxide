# AGC v2: adaptive graph context, cost per correct answer: REJECT

**Verdict: REJECT** under the preregistered rule ([preregistration.md](preregistration.md), frozen
before any scored run; hashes in [results/freeze.txt](results/freeze.txt), re-checked OK after the
runs). AGC v1's REJECT (7eca108) stands.

Both AGC arms fail criterion A (adoption). The agent called `oxide_context` in 5/24 (Ad) and
1/24 (Acm) of its relationship and architecture runs, against the 50% the rule requires. Acm
also fails E (cost). Ad passes V, Q, F and E, but because the agent barely used the tool, its
cost and quality numbers measure "B with an unused tool listed", not AGC.

All numbers below are measured on 144 scored runs: 12 new tasks × 4 arms × 3 reps, OpenCode
2.1.4 with `openai/gpt-6-luna`, 0 technical failures. Statements marked *inferred* are
interpretation.

## Results

| | B (grep/read) | Bcm (+ code mode) | Ad (+ `oxide_context`, direct MCP) | Acm (+ `oxide_context`, code mode) |
| --- | --- | --- | --- | --- |
| Success (F1 ≥ 0.8, 0 false claims) | 0.944 | 0.917 | **1.000** | 0.972 |
| Macro F1 | 0.980 | 0.966 | 0.995 | 0.993 |
| False claims / run | 0.028 | 0.056 | **0.000** | 0.028 |
| CPS (cost units per success) | 11 859 | 17 816 | **10 811** | 15 538 |
| CPS ratio to B (95% bootstrap CI over tasks) | 1 | 1.50 (1.30–1.76) | **0.91 (0.78–1.04)** | 1.31 (1.14–1.47) |
| Tokens ratio (per-task medians) | 1 | 2.92 | 1.09 | 2.55 |
| Seconds ratio | 1 | 1.60 | 1.00 | 1.17 |
| Calls ratio | 1 | 1.47 | 1.13 | 1.10 |
| First-step prompt tokens (median) | 867 | 2 093 | 1 154 | 2 537 |
| Startup s (median, not in latency) | 0.55 | 0.55 | 3.08 | 3.08 |
| grep / read calls per run | 1.86 / 3.92 | 1.94 / 4.17 | 1.75 / 4.14 | 1.78 / 3.89 |
| Repeated reads per run | 1.47 | 1.78 | 1.61 | 1.72 |
| OXIDE calls (graph / outline) | – | – | 8 (0 / 8) | 1 (1 / 0) |
| Used OXIDE: heavy / lookup runs | – | – | 5/24 / 2/12 | 1/24 / 0/12 |

| Criterion | Ad | Acm |
| --- | --- | --- |
| V: technical failures ≤ 10% | pass | pass |
| A: OXIDE in ≥ 50% of heavy runs | **fail** (21%) | **fail** (4%) |
| Q: success ≥ B − 0.05, F1 ≥ B − 0.03 | pass | pass |
| F: false claims ≤ B | pass | pass |
| E: tokens ≤ 1.50, seconds ≤ 1.25, CPS ≤ 1.00 (no premium: neither condition held) | pass | **fail** (tokens 2.55, CPS 1.31) |

Per-task and per-type numbers are in [results/report.json](results/report.json), and every run
is in [results/runs.json](results/runs.json).

## What the evidence says

1. **A capable grep/read baseline leaves little headroom on these tasks.** With the
   workflow guidance in the system prompt, B succeeded in 34/36 runs, with F1 0.98 and 1
   false claim in 36 runs. Its only failures were both on A2 (requests authentication).
   v1's baseline made 1.08 false claims per run; v2's makes 0.03 (*inferred:* the
   difference is the new tasks plus the workflow guidance). It leaves AGC little room to
   pay for itself through grounding.
2. **When the tool is optional, the agent mostly doesn't use it.** The description
   says to use grep and read for lookups and the tool for relationships. The agent
   took this as "grep first" everywhere:
   - **Direct MCP (Ad):** 8 OXIDE calls in 7 of 36 runs, all outline or symbol views
     and none with `relation`. Even on R4 ("callees of `Session.send`"), the one Ad
     call asked for the symbol outline, not `relation='callees'`.
   - **Code mode (Acm):** one call in 36 runs, a `callers` query on R1 (1 732 chars;
     the run succeeded).
   - **Lookups:** 2 of Ad's 12 lookup runs used the tool, which the adaptive
     description advises against.
   - **Unusable targets:** two calls passed targets the tool can't resolve
     ("assertion rewriting", "src/_pytest/python.py:") and got 159- and 162-char
     replies.
3. **Ad's lower CPS is not evidence for OXIDE.** Its CI includes 1 (0.78–1.04). Of
   Ad's 36 runs, 29 made no OXIDE call, and B's two failures were on A2, where Ad never
   called the tool and succeeded 3/3. *Inferred:* the gap is run-to-run variance, not
   the tool.
4. **Direct MCP costs about what it adds to the prompt.**
   - **Prompt:** the tool schema adds 287 first-step prompt tokens (1 154 vs 867).
     Because the tool was rarely called, Ad's tokens ratio is 1.09 and its cost-units
     ratio is 1.005.
   - **Startup:** about 2.5 s per session to spawn the MCP server and connect.
     Indexing these checkouts from scratch is a one-time cost: requests 0.4 s,
     flask 0.5 s, pytest 1.3 s, 58 MB in total, measured under the resource caps.
   - **Per call:** OXIDE answered in a median of 0.26 s, with replies of
     159–1 732 chars.

## Client and wrapper overhead (descriptive)

- **Code mode alone (Bcm/B):** CPS ×1.50, tokens ×2.92, first-step prompt ×2.41, with
  no OXIDE involved.
  - Bcm made 58 `execute` calls in 28 of its 36 runs. They hit
    `tools.opencode.list_mcp_resources` (16), `models` (13) and `read_mcp_resource` (2),
    which is exploration of the wrapper itself.
  - This confirms v1's inference that its ~2× token cost was OpenCode's code mode, not
    AGC.
- **Same tool, code mode vs direct (Acm/Ad):** CPS ×1.44, first-step prompt ×2.20.
- **Correction to v1:** v1 reported that OpenCode 2.1 hides MCP tools outside code
  mode. That was wrong. OpenCode does not wait for MCP servers before the first model
  step, so the tools are missing at step 1. This harness waits for `/api/mcp` to report
  `connected` (see preregistration § Agent and harness), and the tools then appear
  natively.

## Smallest promising variant (inferred, not tested)

The only shape worth keeping is **direct MCP, one tool, no code mode**. Code mode is a
measured ×1.4–1.5 CPS tax whether or not OXIDE is involved. Ad shows the direct tool can sit
in the prompt at about +9% tokens without hurting quality.

What this study could not show is a task where an agent with a capable grep/read workflow
needs the graph. A further study would need both of these:
- tasks where grep/read measurably fails, such as callers through dispatch, or larger
  repositories than pytest;
- a client or prompt where the agent actually calls `relation=`.

Without both, there is no measured benefit to pay for. No production change is
recommended.

## Limitations

- **Narrow coverage:** one client and one model, three Python repositories, 12 tasks
  and 3 reps. The CPS intervals come from 2 000 bootstrap resamples of the 12 tasks.
- **Cost is a proxy:** token-price weights stand in for cost, because the provider
  reports $0. Real billing and cache pricing may differ.
- **Small samples where it matters:** only 8 runs (9 calls) used the tool, too few to
  say whether `oxide_context` helps when it is used.
- **Not tested:** forced or required tool use, different tool descriptions, and other
  clients' MCP handling. The preregistered design held these fixed.

## Reproduce

Scripts are in `scripts/`; data is in `~/Projects/oxide-eval-data/oxide-agc2-eval`.

```
python trial.py check      # task keys resolve in the index
python trial.py run REP 2  # one repetition, 2 at a time, under systemd-run caps
python trial.py verify     # checkouts unchanged
python trial.py grade      # results/runs.json
python3 analyze.py         # results/report.json and the decision
```
