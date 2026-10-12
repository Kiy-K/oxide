# Agent-guided context navigation (A/B/C/E): experiment

Status: **INCONCLUSIVE** (held-out, preregistered rule), 2026-10-12; no
measurable effect of navigation on task success (see Recommendation). Production
retrieval, routing, selection and packing are unchanged; nothing here is
production policy. Numbers are measured unless marked *inferred*.

## Question

Does OXIDE help a coding agent solve tasks more often, or with less
exploration at acceptable cost, when it gives **navigation** (ranked,
source-backed locations and typed relations, with on-demand expansion)
instead of, or besides, a packed **ContextBundle**? The question is task
success, not whether OXIDE can find more code.

## Design

| Variant | What the agent gets on top of the same prompt and tools |
| --- | --- |
| A | nothing (agent only) |
| B | the packed ContextBundle: `build_context` payload, 4,096 `oxide-units-v1` |
| C | a NavigationBundle from the same `ContextRun`: ≤12 locations, `path::Qualified.name [lines]`, one-line preview, why (entry point / routed / selected / expanded), typed relations (calls, refs, imports, implements, tests; ≤3 distinct targets per group), snapshot and derivation ids; no source bodies |
| D | C plus `oxide-nav find / expand / route` as a shell command (dev only) |
| E | C plus the same three operations as read-only MCP tools (`oxide_find`, `oxide_expand`, `oxide_route`); the interactive arm on held-out |

- Agent: Codex CLI 0.162.1, `gpt-6-luna`, reasoning `high`, `codex exec`,
  isolated `CODEX_HOME` (auth only; plugins, apps, hooks, memories,
  multi-agent, browser and web search off), sandbox `workspace-write`
  with network off, empty `HOME` and fixed `PATH`, 30-minute cap. Every
  variant keeps the agent's normal shell, search and edit tools; only the
  OXIDE additions differ.
- Source: tracked files at the task's `base_commit` (`git archive`) in a
  fresh one-commit repository, so upstream history (and the fix) is
  absent. OXIDE indexes exactly those bytes, so every line range it prints
  matches the agent's checkout.
- OXIDE: Phase 4 baseline config, heuristic judge, lexical + structural
  entry points, no embeddings, no JEV, no network.
- Tasks: the ContextBench SWE-bench Verified Python tasks of DecisionBench
  v1 (`tasks.json`), with DecisionBench's repository splits: **dev** 9
  (sympy 5, sphinx 4), **held-out** 19 (django 6, xarray 4, scikit-learn 3,
  pytest 3, astropy 2, requests 1; 21 minus the two matplotlib tasks, whose
  grading images do not unpack under rootless podman, excluded before the
  freeze).
- Success: SWE-bench harness 5.0.2 (`SWE-bench/SWE-bench_Verified` images,
  podman). Gold patches resolve 9/9 dev and 19/19 held-out.
- Pairing: same task, snapshot, prompt and caps in every variant; variant
  order randomized per task and repetition; the task is the bootstrap unit.

Freeze: [preregistration.md](preregistration.md) and the hashes in
[results/freeze.txt](results/freeze.txt) were written before any held-out
agent run (they are committed with the results, so git does not attest the
order).

## Reproduce

Prerequisites: `mise run native:prepare`; the ContextBench worktrees and
parquet under `~/Projects/oxide-eval-data` (see
[../phase-5.md](../phase-5.md)); Codex CLI logged in; podman; `uv`. Data goes
to `$OXIDE_AGENTNAV_DATA` (default `~/Projects/oxide-eval-data/oxide-agentnav`),
outside git: it holds source copies, stores, transcripts and patches.

```bash
D=~/Projects/oxide-eval-data/oxide-agentnav
cd oxide_kernel && cargo build --release -j 2 -p oxide-runtime --example agent_nav && cd ..
uv venv --python 3.11 $D/venv && VIRTUAL_ENV=$D/venv uv pip install swebench==5.0.2 pyarrow
mkdir -p $D/bin $D/home /run/user/$(id -u)/podman
printf '#!/bin/sh\nexec podman "$@"\n' > $D/bin/docker && chmod +x $D/bin/docker   # swebench shells out to `docker`
podman system service --time=0 unix:///run/user/$(id -u)/podman/podman.sock &
# $D/codex-home: auth.json symlink + the config.toml whose sha256 is in results/freeze.txt
S=docs/agent-navigation-eval/scripts
python3 $S/harness.py tasks                                   # tasks.json (committed)
uv run --with pyarrow python $S/harness.py prepare            # export, index (6 GB / 200% CPU cap), bundles
python3 $S/harness.py run dev ABCDE 1 2                       # and rep 2
PATH=$D/bin:$PATH python3 $S/harness.py grade dev 1           # and rep 2
for R in 1 2 3; do python3 $S/harness.py run heldout ABCE $R 2; PATH=$D/bin:$PATH python3 $S/harness.py grade heldout $R; done
python3 $S/analyze.py dev 1 2 && python3 $S/analyze.py heldout 1 2 3
python3 $S/analyze.py --selftest                              # gates() on synthetic rows
```

## OXIDE cost (measured, 28 tasks, this laptop)

| Stage | Median | Max |
| --- | --- | --- |
| Cold: capture, derive, publish (`agent_nav index`) | 6.3 s, 0.53 GiB peak RSS | 35.0 s, 3.36 GiB (sympy, 45k entities) |
| Store on disk | 145 MiB | 729 MiB |
| Warm: open store | 30 ms | 66 ms |
| Warm: `build_context` (route, select, pack) | 422 ms | 674 ms |
| Warm: render NavigationBundle | 42 ms | 105 ms |
| Warm: query process peak RSS | 76 MiB | 120 MiB |
| B payload / C NavigationBundle | 4,079 / 1,457 units | 4,096 / 2,489 units |
| D/E call (smoke): `expand` / `route` | 96–316 ms / 904 ms, ≤93 MiB | |

Each query is a separate process here (store opened per call); the
long-lived runtime of ADR-0003 would remove the open cost.

## Dev (iteration; not part of the decision)

9 tasks × 5 variants × 2 repetitions = 90 runs, all graded
([results/dev-report.md](results/dev-report.md)).

| | A | B | C | D | E |
| --- | --- | --- | --- | --- | --- |
| resolved | 0.556 | 0.500 | 0.556 | 0.556 | 0.611 |
| input tokens vs A (geo-mean ratio, 95% CI) | 1 | 0.86 [0.66, 1.14] | 0.87 [0.73, 1.05] | 0.90 [0.72, 1.14] | 0.87 [0.71, 1.06] |
| tool calls vs A | 1 | 0.77 [0.60, 1.00] | 0.87 [0.73, 1.03] | 0.84 [0.66, 1.05] | 0.85 [0.68, 1.03] |
| OXIDE tool calls | | | | 0 of 18 runs | 0 of 18 runs |

- 7 of 9 tasks give the same outcome in every variant and repetition (4
  always solved, 3 never); only two sphinx tasks vary, and they flip
  between repetitions of the same variant, i.e. agent noise.
- C against B: same tokens (1.01 [0.92, 1.12]), more tool calls (1.13
  [1.03, 1.24]): the shorter NavigationBundle makes the agent read what B
  would have given it.
- The interactive arm was never used: not as a shell command (D), and not
  as native MCP tools (E, the one interface change made on dev). The agent
  opened the NavigationBundle's files with `sed`/`rg` instead.
- Dev gates (C, D): INCONCLUSIVE; A ≥ C and D on success.

## Held-out (preregistered)

19 tasks × 4 variants × 3 repetitions = 228 runs, all graded, none timed
out or errored ([results/heldout-report.md](results/heldout-report.md),
raw rows in [results/heldout-runs.json](results/heldout-runs.json)).

| | A | B | C | E |
| --- | --- | --- | --- | --- |
| resolved (57 runs each) | 0.667 | 0.667 | 0.649 | 0.684 |
| mean input tokens | 92,499 | 114,034 | 89,071 | 105,165 |
| OXIDE prompt units (median) | 0 | 4,087 | 1,468 | 1,468 |
| OXIDE tool calls | | | | 0 of 57 runs |

Paired by task, 95% bootstrap CI (success: difference; others:
geometric-mean ratio):

| pair | success | input tokens | tool calls | wall time |
| --- | --- | --- | --- | --- |
| B vs A | +0.000 [−0.053, +0.053] | 1.18 [1.05, 1.34] | 0.96 [0.84, 1.10] | 1.12 [0.99, 1.28] |
| C vs A | −0.018 [−0.070, +0.035] | 0.97 [0.90, 1.05] | 0.89 [0.81, 0.98] | 1.04 [0.94, 1.16] |
| C vs B | −0.018 [−0.053, +0.000] | 0.82 [0.75, 0.90] | 0.93 [0.85, 1.01] | 0.93 [0.84, 1.03] |
| E vs A | +0.018 [+0.000, +0.053] | 1.10 [1.00, 1.21] | 0.98 [0.88, 1.09] | 1.06 [0.97, 1.16] |
| E vs B | +0.018 [+0.000, +0.053] | 0.93 [0.85, 1.01] | 1.02 [0.95, 1.11] | 0.95 [0.84, 1.06] |
| E vs C | +0.035 [+0.000, +0.088] | 1.12 [1.04, 1.24] | 1.10 [1.03, 1.20] | 1.02 [0.92, 1.13] |

Gates (`gates()`, unchanged since the freeze):

- C: superior no, non-inferior yes, efficient no (token-ratio CI upper
  bound 1.05 vs A; tool-call upper bound 0.98 vs A, 1.01 vs B; the gate
  needs < 0.85 against both), reliable yes.
- E: superior no, non-inferior yes, efficient no, reliable yes.
- Not PROMOTE: neither arm is superior, or non-inferior and efficient,
  against both A and B.
- Not REJECT: E's point success (0.684) exceeds max(A, B) (0.667), which
  the REJECT clause excludes. That margin is one run: A failed
  `pytest-7205` once in three repetitions; E did not.
- Reporting rules: A (0.667) is not ≥ E (0.684), and B (0.667) is not >
  C and E, so neither preregistered note fires, on that same one-run
  margin.
- Result: **INCONCLUSIVE**.

16 of 19 tasks give the same outcome in all 12 runs (10 always solved, 6
never). The other 3 (pytest-10051, -10081, -7205) each have one or two
failures scattered across variants and repetitions. Every success
difference comes from these 5 failed runs out of 228.

Interrupted runs: when the run was paused on 2026-10-11, two rep-2 B runs
were in flight and had no `meta.json`. The harness discarded and re-ran
them on 2026-10-12; that is an infrastructure interruption, not a model
outcome. No other run was repeated.

## Failure analysis

Measured on held-out unless stated otherwise.

- **Failures are fixes, not localization.** 76 of 228 runs failed. 75 of
  them patched a gold file, and the 76th made no change. The 6 tasks that
  never pass (xarray-4687, xarray-7229, sklearn-14629, astropy-13236,
  astropy-13977, requests-6028) were edited in the gold file in every run of
  every variant, with the same file chosen 11–12 times out of 12. Better
  locations cannot help when the agent already edits the right file; the
  patch content is wrong. Dev showed the same pattern (2 of the 3 all-fail
  tasks edited the gold file).
- **The interactive arm is never used.** E (MCP tools) made 0 calls in 57
  held-out runs and 18 dev runs; D (shell command) made 0 calls in 18 dev
  runs. With the right file already found by `rg` within a few commands,
  the agent has no reason to ask for relations. E therefore measures only
  the cost of exposing the tools (their schemas in the context), not
  on-demand expansion. Note that E and C give the agent the same OXIDE
  information, yet differ by +0.035 [0.000, 0.088] in success. That gap is
  the noise floor of this sample.
- **The bundle points near, not at, the fix.** The NavigationBundle names an
  edited file in 12 of 19 held-out tasks (6 of 9 dev), but its top location
  is an edited file in only 4 of 19 (3 of 9). About 29% of its locations
  are test files (21% on dev). The agent still has to search, which
  explains C's small tool-call saving (0.89 vs A) rather than a large one.
- **False completion.** One C run (pytest-10081, rep 3) reported a fix and
  made no edit, the same Codex failure seen once in a dev pilot. It counts
  as unresolved; the other two C runs on that task passed.
- **Ceiling.** The 6 all-fail tasks and the 10 always-solved tasks leave 3
  movable tasks. A 19-task sample cannot resolve effects below about ±0.2,
  as stated in the preregistration.

## Cost / quality trade-offs

Held-out, against A (agent only), same success within noise for all arms:

| Arm | Extra prompt | Input tokens | Tool calls | OXIDE cost per task |
| --- | --- | --- | --- | --- |
| B, packed bundle | ~4,090 units | **+18%** [+5%, +34%] | −4% (n.s.) | cold index (median 6.3 s) + ~0.45 s warm |
| C, NavigationBundle | ~1,470 units | −3% (n.s.) | **−11%** [−19%, −2%] | the same index + ~0.5 s warm |
| E, C + MCP tools | ~1,470 units + tool schemas | +10% [0%, +21%] | −2% (n.s.) | the same, plus a resident MCP server; tools unused |

- B costs tokens and buys nothing measurable here: the agent reads the
  4k-unit payload and then reads the files anyway.
- C is the cheapest OXIDE form. It cuts tool calls by about a tenth and
  uses 18% fewer tokens than B (0.82 [0.75, 0.90]). It does not beat the
  agent alone on tokens, and the tool-call saving misses the
  preregistered 15% threshold.
- E's tools add context on every turn and are never called.
- Wall time does not differ meaningfully for any arm (CIs include 1.0).
  OXIDE's own time (index plus query) is a few seconds, small next to a
  20–30 s agent run, but not zero, and it would only pay if success or
  exploration improved.

## Recommendation

**INCONCLUSIVE**, by the preregistered rule, with no PROMOTE case.

- Navigation-first did not show a meaningful improvement over either the
  agent alone or the packed bundle. The only success difference is one
  run out of 57, and the interactive arm was never exercised.
- The strict REJECT clause did not fire, by that one-run margin. I report
  INCONCLUSIVE rather than reinterpret the rule after seeing the data.
- In practice: do not change SPEC, ADRs or production defaults on this
  evidence. If one-shot context stays in use, these data favor the shorter
  NavigationBundle shape (C) over the 4k packed payload (B): same success,
  18% fewer input tokens. That is a cost observation, not a promotion.
- The on-demand expansion hypothesis (D/E) remains untested in substance:
  this agent never calls the tools. Testing it needs tasks where finding
  the code is the bottleneck (here it was writing the fix), or an agent
  whose policy calls navigation tools. Both are new experiments, not
  reruns of this one.

## Proposed SPEC/ADR changes

None. The results do not support a change to the SPEC, an ADR or a
production default (see Recommendation). No ADR status was changed.

## Limits and blockers

- Two held-out matplotlib tasks could not be graded: their SWE-bench images
  contain files owned by UID 197609, outside this user's subordinate UID
  range, so rootless podman cannot unpack them. Fixing it needs a wider
  `/etc/subuid`/`/etc/subgid` range (root) or `ignore_chown_errors` in the
  user's container storage config; neither was changed.
- One agent, one model, one effort level; Codex's own nondeterminism is
  only partly averaged by 3 repetitions.
- ContextBench's SWE-bench Verified subset of DecisionBench (Python only,
  OXIDE's one language slice); no claim about other languages or task
  types.
- "Files inspected" counts repository paths named in shell commands: a
  proxy, blind to directory-wide `rg` hits and reads inside scripts.
- Tool non-use is a property of this agent and these instructions; an
  agent prompted or trained to call OXIDE first might behave differently
  (not tested: it would change the agent, not OXIDE).

## Files

- `scripts/harness.py`: task list, preparation, agent runs, grading.
- `scripts/analyze.py`: per-run metrics, paired bootstrap, `gates()`.
- `scripts/nav_mcp.py`: experiment-only MCP server for variant E (not
  OXIDE's MCP surface, which belongs to the TS control plane).
- `../../oxide_kernel/crates/runtime/examples/agent_nav.rs`: experiment
  aid over existing kernel APIs (index, bundle, find, expand, route).
- `tasks.json`, `preregistration.md`, `results/freeze.txt`,
  `results/{dev,heldout}-runs.json` (raw per-run rows),
  `results/{dev,heldout}-report.md`.
