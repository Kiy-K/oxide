# AGC v3: tool adoption vs tool utility on structural tasks (preregistration)

Frozen before any scored run (hashes in `results/freeze.txt`). The dev task (`trial.py dev`,
unscored) may change only the harness, never the tasks, truth, arms or rules below.
AGC v1 (7eca108) and v2 (5cf699d) are REJECT; this study keeps both verdicts.

## Question

v2 left one question open: agents rarely invoked `oxide_context` (5/24 heavy runs), on tasks
a capable grep/read baseline already solved (94%). This study asks it on harder tasks, and
pulls apart two things v2 could not:

- **Utility:** does the graph improve correct, grounded answers, or their cost, when it
  is used?
- **Adoption:** does the agent use it when it is merely available, or when it is told
  when to use it?

## Repositories and index

- **Repositories:** xarray and seaborn, from the structural-trial checkouts. Neither has
  been used in an AGC task before.
- **Store:** indexed fresh into `oxide-agc3-eval/data`. Building it took 1.7 s for
  xarray and 1.1 s for seaborn, 49 MB in total, measured under the resource caps.
- **Server:** AGC v2's `oxide_context` server, unchanged: the graph navigation views
  (callers and callees) and the tiered context views (package map, file outline,
  symbol).

## Tasks (`tasks.json`): selected by rule, not by outcome

`scripts/select_tasks.py` picks candidates from names and text alone. It never reads
OXIDE edges or any agent result. It considers only library code (the package directory,
excluding tests) and writes `results/selection.json`, each pool in a seeded order.

**Dispatch candidates.** The goal is calls that only the class hierarchy can sort out.
- A non-dunder method whose name is defined at least 3 times in library code.
- The name is called through `self.`, `cls.` or `super().` at 4–20 sites.
- *Revision, made before the freeze and before any run:* the first rule counted every
  `name(` site. The receivers of most of those calls can't be typed without data flow,
  so their truth couldn't be written. The rule now counts only `self`, `cls` and
  `super()` sites.

**Impact candidates.** The goal is callers up to two hops away.
- A module-level function whose name is defined once in library code.
- It has 3–8 library call sites.

**Picking.** I went down each seeded pool and wrote the truth by hand from the source,
without consulting OXIDE. The rule takes 2 per type per repository, at most one per
short name and one per class. It skips a candidate only when its truth has fewer than 2
items.

| Pool | Candidate | Outcome |
| --- | --- | --- |
| xarray dispatch | `Variable.reduce` | taken |
| | `FileManager.close` | skipped: an abstract method both subclasses override, so truth 0 |
| | `AbstractDataStore.close` | skipped: only `__exit__` decides the receiver, so truth 1 |
| | `DataArrayGroupBy.apply` | taken |
| seaborn dispatch | `Dot._resolve_properties` | taken; the other 3 candidates share its name |
| impact (both repositories) | the first two in each pool | taken |

That gives **7 tasks**. The seaborn impact tasks, I3 and I4, turned out to sit in a single
file each and are easy to grep. They stay in, because the rule chose them; results are
also reported by task type.

| Id | Repo | Type | Target | Truth (n) | What makes grep/read hard |
| --- | --- | --- | --- | --- | --- |
| D1 | xarray | dispatch | `Variable.reduce` | 3 | 8 `reduce` methods; reached through reduce wrappers injected into `ImplementsArrayReduce`, and `var.reduce` / `self.variable.reduce` |
| D2 | xarray | dispatch | `DataArrayGroupBy.apply` | 4 | 5 `apply` methods; base-class `self.apply`, a `super().apply` in a subclass override; the `DatasetGroupBy` calls are traps |
| D3 | seaborn | dispatch | `Dot._resolve_properties` | 2 | 4 definitions; `DotBase` self-calls reach it, while `Dots`' `super()` and `BarBase` calls do not |
| I1 | xarray | impact | `formatting.summarize_variable` | 5 | callers reached through a `summarizer` parameter and `functools.partial` |
| I2 | xarray | impact | `_get_broadcast_dims_map_common_coords` | 4 | callers in 3 modules; `broadcast(` text also matches `np.broadcast` |
| I3 | seaborn | impact | `widgets._init_mutable_colormap` | 5 | one file |
| I4 | seaborn | impact | `_testing.assert_artists_equal` | 2 | one file |

**Truth rules** (stated in each question):
- **Dispatch:** a library function counts if it contains a call that can execute the
  target at run time. That covers calls through `self`, `cls` and `super()` that the
  class hierarchy resolves to the target, and calls on objects that are clearly of the
  class. Calls that always reach a different same-named method are false.
- **Impact:** a library function counts if it calls the target directly, or calls one of
  its direct callers. A call through a parameter counts as a call to every library
  function passed for that parameter. *Neutral* items are plausible but not asked for
  (I1: the module-level partials and `coords_repr`); they count neither way.

## Arms

| Arm | Tool | System prompt | What it measures |
| --- | --- | --- | --- |
| B | none | AGC v2's capable grep/read prompt | baseline |
| D | `oxide_context`, direct MCP | the same as B | organic adoption, v2's shape |
| G | `oxide_context`, direct MCP | B + guidance by question type (below) | organic adoption with guidance |
| F | `oxide_context`, direct MCP | G + "your first tool call must be oxide_context …" | **diagnostic:** utility when used; never counted as adoption |

**What stays fixed.** The message, model, tool description and isolation are identical in
every arm. The arms differ only in the system prompt and in whether the server is attached.

**G's guidance** (`trial.py` `GUIDANCE`) is fixed by question type, never written for a task:
- For callers or impact, call `relation='callers'` first, with `depth=2` for callers of
  callers.
- Read only to confirm a listed site or settle an unexplained match, and don't grep the
  name again.
- For what X calls, use `callees`.
- For orientation, ask for a file or class outline.
- For a single definition, grep and read.

## Harness

The harness is AGC v2's, imported unchanged (`opencode serve`, wait for MCP `connected`,
`opencode run --server`, export), with one change made during dev:

- **The settle.** In one of 3 dev G runs, the server reported `connected` but the model's
  first step went out without the tool schema: 1 026 prompt tokens, against B 863 + 163
  for the guidance + 287 for the schema. In v2 all 36 Ad runs carried the schema (B +
  287 on every task), so v2 is unaffected. The harness now waits 2 s after `connected`,
  and both later dev G runs carried the schema.
- **The validity check.** Every server-arm run must carry the schema at step 1: its first
  prompt minus B's first prompt on the same task, minus the arm's prompt tokens
  (G 163, F 194), must be ≥ 143. A run that fails is a harness failure, not evidence. Its
  record is set aside (renamed, not deleted) and the run is repeated before analysis, the
  same way rate-limited runs are retried.
- **Limits:** timeout 900 s; 2 runs at a time under `systemd-run` (MemoryMax 3G,
  CPUQuota 200%).

## Measures

**Correctness.** Grading is AGC v1's: answer lines map to their innermost entity, then F1,
false claims, and **success**. Success means: not a technical failure, F1 ≥ 0.8, and 0
false claims.

**Cost and effort.**
- Cost units: `input + 0.1·cache_read + cache_write + 8·(output+reasoning)`.
- CPS (cost per success) and its ratio to B, with a bootstrap CI over tasks.
- Tokens, wall seconds and tool calls, each as a ratio to B.
- grep, glob and read calls, and **redundant reads** (re-reading a file already read).

**Tool use.**
- **Organic use** (D and G only): the share of runs with at least one OXIDE call, and with
  at least one graph call.
- **F's compliance:** the first tool call is `oxide_context`. F needs ≥ 90% compliance, or
  the diagnostic is invalid.

**Exposure vs invocation** (descriptive):
- **Exposure:** the first-step tokens the arm adds over B. As a cost estimate, those tokens
  × (1 + 0.1 × (steps − 1)), assuming later steps read them from cache.
- **The measured split:** the run's cost over B's median for the same task, taken
  separately for runs that never called the tool (exposure only) and for runs that did.
- **The call itself:** the tool's output size (~chars/4 tokens) and its server time.

## Design and stopping

- **Stage 1:** 1 repetition, 7 tasks × 4 arms = 28 runs, in a seeded order.
  `analyze.py stage1` then decides whether to go on (the run must be valid: ≤ 10%
  technical failures per arm, no missing schema, F compliance ≥ 90%):
  - **Adoption signal:** G calls OXIDE in ≥ 50% of its runs, and in more of them than D.
  - **Utility signal:** F has at least one more success than B, and F's false claims per
    run are ≤ B's.
  - **STOP** if neither signal holds: report stage 1 and the decision "no improvement".
    Otherwise **CONTINUE**.
- **Stage 2:** 2 more repetitions, 56 runs, for 21 runs per arm in total.

## Decision rule (all 3 repetitions)

**Criteria.**
- **U (utility, F vs B):** F's success rate is ≥ B + 0.10 with false claims ≤ B; *or* F's
  success rate is ≥ B − 0.05 and its CPS ratio is ≤ 0.90.
- **A (adoption, G):** G's OXIDE-use rate is ≥ 0.50, and ≥ D's rate + 0.20.
- **P (product gate, G vs B):** A holds, and also:
  - success ≥ B − 0.05;
  - macro F1 ≥ B − 0.03;
  - false claims ≤ B;
  - tokens ratio ≤ 1.50 and seconds ratio ≤ 1.25;
  - CPS ratio ≤ 1.00. The *premium* allows ≤ 1.20 when G's success is ≥ B + 0.10, or when
    B has ≥ 0.25 false claims per run and G has at most half as many.

**Next decision.**

| Outcome | Next |
| --- | --- |
| P | PROMOTE G: propose an ADR for a direct `oxide_context` tool with type guidance. No code is changed here. |
| U, not A | ADOPTION-BOUND: the graph helps when it is used, and the agent doesn't pick it. The next step is client-side guidance or integration, not tool changes. |
| U and A, not P | COST-BOUND: the next step is output size and format. |
| not U | STOP: the graph doesn't help on these tasks even when it is used. Close agent-facing AGC and keep the graph API-only. |

If validity fails: INVALID.

## Not tested

- Other models or clients.
- Non-Python repositories.
- Editing tasks.
- Real billing.
- More than 7 tasks: the stage-1 decision rests on 7 runs per arm, so it is noisy by
  design and gates only whether to continue.
