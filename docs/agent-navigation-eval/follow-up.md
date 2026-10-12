# Agent-guided navigation: follow-up diagnosis (2026-10-12)

Status: **diagnostic only; R&D iteration closed 2026-10-12.** The preregistered held-out verdict stays
**INCONCLUSIVE** ([README.md](README.md)); no frozen file, result, gate, SPEC,
ADR, production default or model configuration changed. The 19 held-out
tasks are now diagnostic material: nothing here was tuned on them, and no
later run on them may be presented as independent validation. Numbers are
measured unless marked *inferred*.

Question: is the INCONCLUSIVE result due to (1) too little power, (2) MCP
integration or tool discovery, (3) a workload with little structural
navigation demand, or (4) no real benefit from OXIDE navigation?

## Answer

| Explanation | Finding | Evidence |
| --- | --- | --- |
| 1. Power | **Partly.** The design could detect a uniform effect of about 0.05–0.09 (80% power), but only 3 of 19 tasks could move at all | 10 tasks always solved, 6 never; `power.py` |
| 2. MCP / discovery | **Yes, for arm E.** The tools worked when called, but Codex never put them in the model's tool list | discovery runs; input tokens identical with and without the server |
| 3. Low structural demand | **Yes.** Failures are fix-design and reasoning errors in the right file, not missing context | 0 of 76 failed runs are localization misses; 1 of 9 failing tasks plausibly misses a dependency |
| 4. No real benefit | **Not tested.** Neither the information bottleneck (3) nor the interactive arm (2) was present, so the run could not show a benefit or its absence | — |

## Task 1: MCP integration audit

Wiring: `harness.py run_one` passes
`-c mcp_servers.oxide.command="python3" -c mcp_servers.oxide.args=[nav_mcp.py, agent_nav, store, log]`
to `codex exec` (Codex CLI 0.162.1). `nav_mcp.py` is a stdio JSON-RPC server
with three read-only tools (`oxide_find`, `oxide_expand`, `oxide_route`,
`readOnlyHint`). Every call is logged by `agent_nav` to `nav-calls.jsonl`, and
Codex emits an `mcp_tool_call` item in `events.jsonl`. The binary hash still
matches `results/freeze.txt`.

| Check | Result | How |
| --- | --- | --- |
| Protocol | **pass** | `initialize`, `tools/list` (3 tools), `tools/call` (`mcp_audit.py direct`, 5 held-out tasks) |
| Correct evidence | **pass** | all 73 printed `path::name [a-b]` locations open at a `def`/`class` of that name in the task's source; `expand` non-empty for every edited symbol; probe names from gold-patch hunk headers |
| Error signalling | **defect (minor)** | an unknown entity returns `MissingEntity(...)` text with `isError: false`, because `agent_nav` exits 0 |
| Agent discovery | **fail** | asked to list its `oxide*` tools without calling any, the agent replied "None." (`mcp_audit.py agent`, discover). It described MCP tools as deferred, reachable only through a code-mode `ALL_TOOLS` lookup. With `features.code_mode_host=false` it still answered NONE |
| Schemas in context | **absent (measured)** | first-turn input tokens: 9,776 / 9,818 with the server, 9,810 without it. The three schemas (~400 tokens) were never in the model's context |
| Agent invocation | **pass** | asked to call `oxide_find` by exact name, the agent did; `mcp_tool_call` completed, 1 log line, correct output (`mcp_audit.py agent`, forced; diagnostic, not scored) |
| Observability | **pass** | `mcp_tool_call` items and `nav-calls.jsonl` both record calls; the 57 held-out E runs contain neither, nor any mention of an `oxide_` tool or `ALL_TOOLS` |

So **MCP status: working but not discoverable.** The server, evidence,
invocation and logging work. In E's configuration Codex keeps the tool
schemas out of the model's context: the agent learned of the tools only from
one prompt sentence, and could reach them only by name or through deferred
lookup. E therefore did not test on-demand navigation as a first-class tool.

Correction to the README: it says E measures "the cost of exposing the tools
(their schemas in the context)". The schemas were not in the context, so E's
+10% input tokens against A (and +12% against C, which gets identical OXIDE
information) has another cause. *Inferred:* run-length noise; the
preregistered results are unchanged.

This does not explain everything. On dev, arm D printed the full CLI usage in
the prompt, and the agent called it 0 times in 18 runs. Even a visible tool
went unused on these tasks.

Environment drift (benign, *inferred*): Codex appended one
`[projects."…/repo"] trust_level = "trusted"` entry per run to the frozen
`codex-home/config.toml` (322 entries), so its SHA-256 no longer matches
`freeze.txt`. No other key appears to have changed, but the original bytes
cannot be recovered to prove it. A future harness should copy CODEX_HOME per
run, as `mcp_audit.py` does.

## Task 2: Failure attribution

`attribution.py` measures, for each of the 76 failed held-out runs, which
ContextBench gold regions the agent was **shown**: the share of each region's
distinctive lines (≥12 characters) that appear verbatim in the prompt or in
any command output. This separates finding the file from reading the regions
inside it. Edit regions overlap a gold-patch hunk; support regions are the
other gold context (callers, helpers, options, tests).
[results/attribution-heldout.json](results/attribution-heldout.json).

Measured on the 76 failed runs:

- Gold file named in the agent's commands: **76/76**. Patch touches a gold file: **75/76** (the 76th made no edit).
- Edit regions shown (≥0.5): 61/76. The 15 exceptions are 11 sklearn-14629 runs that patched the caller instead (below), 2 pytest-10051 runs where the unseen "edit" hunk is a blank-line deletion, and one run each of xarray-7229 and requests-6028.
- Rule categories (fixed in the script docstring before it was run): not_inspected 59, indeterminate 15, fix_wrong 1, integration 1, localization **0**.
- Control: resolved runs also skip support regions. They saw 58% of support regions (173/298) against 43% (129/302) for failed runs, and saw all of them in 82/152 runs against 2/76. That gap is across tasks and confounded by difficulty, so "not_inspected" is a correlate, not a cause. Hence the hand review below.

Hand review of all 9 failing tasks (patch, hidden test and issue read for each):

| Task | Failed runs | Category | Why |
| --- | --- | --- | --- |
| astropy-13236 | 12 | incorrect patch despite sufficient evidence | The issue proposes a FutureWarning now and removal in 5.2; the hidden test expects removal. The agent added the warning; the exact code is quoted in the issue |
| sklearn-14629 | 12 | incorrect patch despite sufficient evidence | The agent fixed the call site in `_validation.py` the error points to; the hidden test checks a new `MultiOutputClassifier.classes_` |
| xarray-4687 | 12 | incorrect patch despite sufficient evidence | The hidden test calls a new `keep_attrs=` kwarg; the agent hard-coded `keep_attrs=True`/`"drop_conflicts"`. `apply_ufunc`'s keep_attrs handling was shown 12/12; the `_get_keep_attrs` convention (options.py) only 3/12 (weak information candidate) |
| xarray-7229 | 12 | inspected but misunderstood | The fix needs a redesign of attrs propagation to coords; agents tried identity-based lambdas on the shown code |
| requests-6028 | 12 | inspected but misunderstood | Wrong root cause: patched `get_auth_from_url` although `prepend_scheme_if_needed` was shown 11/12. The cause lies in urllib3's `parse_url`, outside the repository, so no repository index can supply it |
| astropy-13977 | 12 | evidence available but not inspected (plausible) / indeterminate | Partial fix (8/20 F2P failing, up to 4 P2P broken). The exception paths of `converters_and_unit` (converters.py 293–332) were shown in 1/12 runs. OXIDE would not have supplied them: they are not in the NavigationBundle, and `expand Quantity.__array_ufunc__` lists `converters_and_unit` as **unresolved** (package re-export; `self._result_as_quantity` is unresolved too) |
| pytest-10051 | 2 | incorrect patch despite sufficient evidence | Changed `reset()` semantics and broke a P2P test; every relevant region was shown |
| pytest-7205 | 1 | incorrect patch despite sufficient evidence | `saferepr` applied to bytes only; the other two A runs and all other arms applied it generally |
| pytest-10081 | 1 | agent behaviour (false completion) | Reported a fix and made no edit (C, rep 3); no tool or OXIDE error |

Totals: 39 incorrect patch despite sufficient evidence, 24 inspected but
misunderstood, 12 plausibly not inspected (one task), 1 false completion, 0
missing evidence, 0 integration or tool failures. **At most 1 of 9 failing
tasks (12 of 76 runs) has a plausible repository-context bottleneck, and
OXIDE's current relations would not have closed it.** The hand categories are
judgment; the region coverage they rest on is measured.

## Task 3: Benchmark redesign

### Why the old set could not answer the question

- Workload: by the structural rule below, 3 of the 19 held-out tasks are structural (django-16136, xarray-4687, sklearn-14629). Every gold patch edits one source file. Mean agent run: 26–30 s, 5 tool calls, 2 files named (measured).
- Ceiling: 16/19 tasks are deterministic across all 12 runs (10 always solved, 6 never).
- Power (`power.py`): within-cell seed variance s_w² = 0.0175. Paired task-difference variance 0.006–0.018, so the minimum detectable effect at 80% power was 0.05–0.09, below the preregistration's ±0.2 guess. Power was adequate for a uniform effect; the effect had no room to appear.

### Strata (preregistered here; `strata.py`, [results/followup-strata.json](results/followup-strata.json))

Source: SWE-bench Verified (cached, gradeable with the existing podman
setup). Eligible: not named anywhere under `docs/` (that excludes all 30
DecisionBench / agent-nav tasks), not matplotlib (ungradeable here), not
sympy or sphinx (the DecisionBench dev repositories OXIDE was iterated on).
**328 eligible tasks**, 30% structural.

- **Stratum A, representative:** a seeded random order of all 328 eligible tasks.
- **Stratum B, structurally demanding:** any of the following, computed from the gold patch only, never from OXIDE output or outcomes:
  - B1: ≥2 non-test source files edited;
  - B2: ≥2 distinct enclosing symbols with ≥10 changed lines;
  - B3: a changed `def` signature.
  - **99 tasks** qualify (B1 50, B2 91, B3 25): django 61, sklearn 10, pytest 8, astropy 7, xarray 6, pylint 5, seaborn 2. Difficulty: 14 under 15 min, 56 at 15–60 min, 29 over an hour.
- Repository hold-out: only pylint and seaborn (7 stratum-B tasks) are repositories OXIDE has never touched. A full repository-held-out split is not practical on SWE-bench Verified; report fresh-repo tasks as a sub-group, and exclude the OXIDE dev repositories as above.
- Patch shape is not information demand: xarray-4687 and sklearn-14629 are stratum-B by shape and still failed on fix design. Stratum B therefore needs the feasibility gate below before any scored run.

### Sample size (paired, task-clustered, seeds averaged within task)

Var(dᵢ) = s_τ² + 2·s_w²/k; the effect is concentrated on a share f of tasks
that each gain g, so MDE = f·g and s_τ² = g²f(1−f). The formula is checked by
simulation: designed for 80% power, it gives 0.83. Tasks per stratum for 80%
power:

| MDE | f, g | seed noise s_w² | k=1 | k=2 | k=3 | k=3, α=0.025 (vs A and B) |
| --- | --- | --- | --- | --- | --- | --- |
| 0.15 | 0.30, 0.50 | 0.0175 (old set) | 31 | 25 | 23 | 28 |
| 0.15 | 0.30, 0.50 | 0.10 | 89 | 54 | 42 | 51 |
| 0.10 | 0.25, 0.40 | 0.0175 | 52 | 38 | 33 | 40 |
| 0.10 | 0.25, 0.40 | 0.10 | 181 | 103 | **76** | **92** |
| 0.10 | 0.25, 0.40 | 0.20 | 338 | 181 | 129 | 156 |
| 0.05 | 0.25, 0.20 | 0.10 | 652 | 338 | 233 | 282 |

A non-ceiling stratum should sit near s_w² ≈ 0.10 (*inferred*: mid-range
tasks are noisier than the old set). At that noise, detecting +0.10 needs
about **76–92 tasks × 3 seeds**, nearly the whole 99-task stratum-B pool. +0.15
needs 42–51. +0.05 is out of reach on SWE-bench Verified. If the feasibility
sample shows s_w² ≈ 0.2, +0.10 is out of reach too.

### Cost (*inferred* from held-out per-run means)

Per run, held-out: 89–114k input tokens (≈85% cached; 15–18k uncached),
1.5–2.1k output plus reasoning tokens, 26–30 s. Harder stratum-B tasks
probably cost 1.5–3× that (*inferred*). A full design, stratum B at 80 tasks
× 3 arms (A, C, E with visible tools) × 3 seeds plus stratum A at 40 tasks ×
3 arms × 2 seeds, is about 960 runs:

- tokens: roughly 0.1–0.3 B input tokens (mostly cached) and 2–6 M output tokens;
- time: about 4–12 h of agent time at 2 in parallel, plus SWE-bench grading;
- downloads: new per-instance images (several GB) for about 120 tasks.

Codex here runs on the user's subscription; no dollar price is stated
because the model's API price was not checked.

### Feasibility sample (not run; needs authorization)

Run 6 stratum-B tasks plus 4 non-structural stratum-A tasks (the first in
`feasibility_sample`), arms A and C, 2 seeds: 40 runs, about 1–2 h. This
needs a new harness (the frozen `harness.py` covers only the old 28 tasks
and their worktrees), repository clones at `base_commit`, and new images.

Go/no-go, fixed now:

1. stratum-B baseline success (A) between 0.2 and 0.8;
2. ≥25% of stratum-B failures attributable to information (`attribution.py` localization or not_inspected, confirmed by hand);
3. an E arm passes the discovery gate below.

If 1 or 2 fails, the benchmark cannot test the navigation hypothesis on SWE-bench Verified: stop.

Discovery gate for any interactive arm. Before scoring, the tool schemas must
be in the model's first-turn context. Check either the input-token delta with
and without the server, or a no-call listing run. Use a client
configuration or agent that exposes MCP tools directly, or the visible CLI
arm (D). Tool use is never forced in scored runs.

## Task 4: Decision

- **MCP:** working (protocol, evidence, invocation, observability verified) but **not discoverable** in E's Codex configuration. The tool schemas were never in the model's context.
- **Failure attribution:** 0/76 localization failures; 63/76 are fix-design or reasoning errors with the edit region shown; 12/76 (one task) plausibly miss an in-repo dependency, which OXIDE's relations leave unresolved; 1 false completion.
- **Context bottleneck:** not plausible on this workload; at most 1 of 9 failing tasks.
- **Can the proposed benchmark test it:** only if the feasibility gate passes. Patch-shape stratification alone does not guarantee information demand (two stratum-B-shaped held-out tasks failed on fix design).
- **Sample size and cost:** about 76–92 stratum-B tasks × 3 seeds × 3 arms for +0.10 at 80% power (about 960 runs with stratum A; 0.1–0.3 B input tokens; 4–12 h agent time). That is close to the whole eligible pool.
- **Recommendation: close this R&D iteration as INCONCLUSIVE.** Keep `agent_nav` and `nav_mcp.py` as the optional, experiment-only navigation path (no production wiring). Any future promotion needs a new, adequately powered held-out evaluation that passes the feasibility and discovery gates above. Further OXIDE development does not wait on it.

## Reproduce

```bash
S=docs/agent-navigation-eval/scripts
python3 $S/mcp_audit.py direct SWE-Bench-Verified__python__maintenance__bugfix__2607643c   # also 0b74a2c7 9f3a5677 e989ba2d 512ce46c
python3 $S/mcp_audit.py agent  SWE-Bench-Verified__python__maintenance__bugfix__2607643c \
  ~/Projects/oxide-eval-data/oxide-agentnav/mcp-audit      # 2 paid diagnostic runs, never scored
python3 $S/attribution.py heldout 1 2 3 > docs/agent-navigation-eval/results/attribution-heldout.json
python3 $S/power.py
HF_DATASETS_OFFLINE=1 ~/Projects/oxide-eval-data/oxide-agentnav/venv/bin/python $S/strata.py \
  > docs/agent-navigation-eval/results/followup-strata.json
```

The no-server and `code_mode_host=false` discovery runs, and the
"list every callable tool" run, were one-off `codex exec` calls with the
same flags as `mcp_audit.py agent`. Their transcripts are in
`~/Projects/oxide-eval-data/oxide-agentnav/mcp-audit/` (outside git).

## Remaining blockers

- An E arm with MCP tools in the model's context: needs a client or configuration where Codex does not defer MCP schemas, or a different agent. Not found here.
- The feasibility sample (above): needs a new harness, clones, images, and authorization.
- `agent_nav` exits 0 on unknown entities, so MCP reports `isError: false`. Experiment-only code, frozen; noted, not fixed.
- OXIDE leaves calls through package re-exports and `self.` methods unresolved (astropy-13977). That limits `expand` for exactly the dependency case. It is a kernel resolution question for its own task, not changed here.
