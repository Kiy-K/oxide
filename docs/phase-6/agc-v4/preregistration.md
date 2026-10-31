# AGC v4: one tool or two, natural vs forced use, on diverse tasks (preregistration)

Frozen before any scored run (hashes in `results/freeze.txt`). The dev task may change only
the harness. Verdicts kept unchanged:
- AGC v1 (7eca108): REJECT.
- AGC v2 (5cf699d): REJECT.
- AGC v3 (0381565): PROMOTE G.

The post-hoc v3 analysis (`scripts/v3_dg.py`, `results/v3_dg.json`) explains why D beat
G. It doesn't change that verdict.

## Questions

1. **Interface.** [ADR-0013](../../adr/0013-graph-navigation-in-oxide-search.md) proposes
   graph navigation and tiered context as modes of the existing `oxide_search` tool. Is
   that single shape at least as discoverable and effective as a separate `oxide_context`
   tool? The single shape is preferred unless the split shape shows a clear advantage
   (defined below).
2. **Promotion.** Does the chosen shape, used *naturally* with no guidance, beat a capable
   grep/read baseline on correct, grounded answers per unit of cost?
3. **Utility.** Separately, with use *forced*, does the tool help when used? Forced use
   never counts as adoption.

## Repositories, store and server

**Repositories.** pytest and flask (Python) and vue-core (TypeScript), from the
structural-trial checkouts. They were indexed fresh into `oxide-agc4-eval/data`:

| Repository | Index time | Files |
| --- | --- | --- |
| vue-core | 2.6 s | 467 |
| flask | 0.6 s | 80 |
| pytest | 1.4 s | 212 |

The store is 75 MB in total.

**Server** (`scripts/server.py`). It starts from production's MCP app
(`oxide.mcp_server.server`) and calls production's `oxide_search` function for
`mode=search|context`. It has two shapes:

- **single:** `oxide_search` with `mode` adding `callers`, `callees` and `outline`. These
  are AGC v2's graph and tiered views, and the extra description recasts v2's tool text
  as modes.
- **split:** production's `oxide_search`, unchanged, plus AGC v2's `oxide_context` with
  its v2 description.

The graph and outline output is the same text in both shapes. Tool descriptions total
1 214 characters (single) and 1 358 (split). Neither shape ships system-prompt guidance,
and neither suggests a fixed depth (`depth` defaults to 1).

## Tasks (`tasks.json`): selected by rule, not by outcome

**Pools.** `scripts/select_tasks.py` builds seeded candidate pools from names and text
only. It considers library code only and excludes every v2 and v3 target. There are 5
types (rules in its docstring): lookup, callers, impact (two hops), callees, and overrides.

**Picking.** One target per slot was planned up front:
- Python: lookup, callers and callees on pytest; impact and overrides on flask;
- TypeScript: lookup, callers and impact on vue-core.

I took the first candidate in each seeded pool, wrote truth by hand from the source
without consulting OXIDE, and skipped a candidate only if its truth had fewer than 2
items (lookups have exactly 1).

**Skipped candidates:**
- pytest callers `ExceptionChainRepr.toterminal`: 1 decidable caller.
- vue-core callers `createSuspenseBoundary.next`: 1 caller. The `next(...)` calls in
  `Suspense.ts` reach a renderer parameter instead.
- flask impact `render_template`: no library callers.

| Id | Lang | Type | Target | Truth (n) | What makes it hard |
| --- | --- | --- | --- | --- | --- |
| L1 | py | lookup | `Parser.getgroup` (pytest) | 1 | none (grep-friendly); tests restraint |
| L2 | ts | lookup | `pushScopeId` (vue) | 1 | `popScopeId` next to it |
| C1 | py | callers | `ReprFuncArgs.toterminal` | 2 | 15 `toterminal` definitions; `self.toterminal` in a base class; a declared attribute type |
| C2 | ts | callers | `VueElement._createVNode` | 2 | a same-named function `_createVNode` in `vnode.ts` and its wrappers |
| I1 | py | impact | `get_debug_flag` (flask) | 8 | second hop through `make_config` and `load_app`, which is reached through `info`/`ScriptInfo` receivers |
| I2 | ts | impact | `getObjectOrArrayExpressionKeys` | 5 | across two files; a nested caller inside `compileScript` |
| E1 | py | callees | `BaseReport.longreprtext` | 2 | `self.toterminal` among 15 definitions; a class constructor |
| O1 | py | overrides | `TagMarkup` (flask) | 3 | none (one file) |

**Question wording.** Callers questions ask for direct callers only ("not their own
callers"); v3's D1 was ambiguous on this. Impact questions say "at most two steps", and
count calls through parameters. The neutral items in I1 are `FlaskGroup.__init__`, which
passes `run_command` as a value, and `cli.py`'s module level.

## Arms (no usage guidance in any arm)

| Arm | Tools | System prompt | Measures |
| --- | --- | --- | --- |
| B | none (read, grep, glob, list) | v2's capable grep/read prompt | baseline |
| S | `oxide_search` single shape | the same | natural use, ADR-0013's shape |
| T | `oxide_search` + `oxide_context` (split) | the same | natural use, separate tool |
| F | single shape | + "Your first tool call must be oxide_search." | diagnostic: utility when used |

## Harness

- **Runner:** AGC v2's (`opencode serve`, wait for MCP `connected`, then
  `opencode run --server`), with v3's 2 s settle.
- **Model:** OpenCode 2.1.4 with `openai/gpt-6-luna`; 2 runs at a time under `systemd-run`
  (MemoryMax 3G, CPUQuota 200%); timeout 900 s.
- **Grading** is v1's scoring, with a citation pattern and owner lookup that also read
  `.ts` paths: the innermost symbol whose span holds the line, as before.
- **Tool schema at step 1.** Every server-arm run must carry it: its first-step tokens
  minus B's on the same task must be at least half the dev-measured addition (S 615,
  T 702, F 633). A run that fails is a harness failure: its record is renamed and the run
  repeated.

## Measures

**Correctness.** Success (F1 ≥ 0.8, 0 false claims), macro F1, false claims per run,
precision.

**Cost and effort.** Cost units (v2's price proxy); CPS and its ratio to B with a
bootstrap CI over tasks; tokens, seconds and calls ratios; grep and read calls; redundant
reads.

**Discoverability and adoption** (S and T, natural use):
- the share of structural runs (callers, impact, callees, overrides) that used a
  navigation mode (`callers`, `callees` or `outline`);
- the share of lookup runs that used any OXIDE call, or a navigation mode;
- calls per mode;
- the first tool called.

**F's compliance:** the first call is `oxide_search`; it must be ≥ 90%.

**Exposure vs invocation:** the first-step tokens each arm adds, and cost relative to B,
split into runs with and without an OXIDE call.

**By group:** per task type, per language and per task.

## Design and stopping

- **Stage 1:** 1 repetition, 8 tasks × 4 arms = 32 runs. `analyze.py stage1`:
  - it must be valid: ≤ 10% technical failures per arm, no missing schema, F compliance
    ≥ 90%;
  - **CONTINUE** if S or T used a navigation mode in ≥ 50% of its structural runs, *or*
    F has at least one more success than B with false claims ≤ B's;
  - otherwise **STOP**, and report stage 1.
- **Stage 2:** 2 more repetitions (64 runs), for 24 runs per arm.

## Decision rule (all repetitions)

**Interface.** Pick T only if the split shape has a clear advantage:
- T's success rate is ≥ S's + 0.10 and T's CPS is ≤ S's; *or*
- T's navigation use on structural tasks is ≥ S's + 0.25 and T's success is ≥ S's − 0.05.

Otherwise pick **S**.

**Gate P for the picked shape X against B:**
- A: X uses a navigation mode in ≥ 50% of its structural runs;
- success ≥ B − 0.05;
- macro F1 ≥ B − 0.03;
- false claims ≤ B;
- tokens ratio ≤ 1.50 and seconds ratio ≤ 1.25;
- CPS ratio ≤ 1.00. The premium allows ≤ 1.20 when X's success is ≥ B + 0.10, or when B
  has ≥ 0.25 false claims per run and X has at most half as many.

**Utility U (F vs B):** F's success is ≥ B + 0.10 with false claims ≤ B; *or* F's success
is ≥ B − 0.05 and its CPS ratio is ≤ 0.90.

**Decision.**

| Outcome | Decision |
| --- | --- |
| P | **PROMOTE X**: ADR-0013 may move to Accepted, in shape X, at the maintainer's word. Implementation follows separately |
| U, not A | **ADOPTION-BOUND**: ADR-0013 stays Proposed |
| U and A, not P | **COST-BOUND**: ADR-0013 stays Proposed |
| not U | **STOP**: withdraw ADR-0013; graph navigation stays API-only |

If validity fails: INVALID. Nothing in `src/oxide`, MCP, CLI or the public API changes in
this study.

## Not tested

- Other clients or models.
- Go and Rust.
- Editing tasks.
- Real billing.
- The production implementation of ADR-0013: this study uses AGC v2's views, not
  `Repository.impact`.
