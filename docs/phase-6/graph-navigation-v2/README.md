# Agent-facing graph navigation, revisited at HEAD (2026-10-25): no interface change

The question repeats [graph-navigation](../graph-navigation/README.md) (5ed725d, Stage 1
gate FAIL): can explicit, source-backed graph paths from `Repository.impact` improve
agent architectural reasoning and impact analysis, without touching ranking? That study,
its reassessment ([agc](../agc/README.md) Part 1) and four agent trials
([agc](../agc/README.md), [agc-v2](../agc-v2/README.md), [agc-v3](../agc-v3/README.md),
[agc-v4](../agc-v4/README.md)) already answer most of it. This note re-checks the
evidence at HEAD (b7f7959, after the NetworkX change) and measures one new completeness
lead. Numbers are measured unless marked *inferred*. Nothing in `src/oxide` changed.

**Recommendation.**
- **No graph mode, tool or public field.** The blocker is still completeness, not
  representation.
- **Keep [ADR-0013](../../adr/0013-graph-navigation-in-oxide-search.md) Proposed.**
- **One small resolver fix is available** (below). It is correct on every edge audited,
  but barely moves the impact metrics, and it needs a derivation version bump with
  baseline identity amendments. That is left to the maintainer.

## References reviewed, and what each implies for OXIDE

| Reference | Relevant design | Implication |
| --- | --- | --- |
| LSP 3.17 call hierarchy | `CallHierarchyIncomingCall {from, fromRanges[]}`; partial results only as streaming (`partialResultToken`), no "incomplete" flag | A witness per edge is standard. Completeness has to be stated by the server itself |
| SCIP `scip.proto` | `Occurrence` with `SymbolRole` bits; `Relationship.is_reference` / `is_implementation` kept separate; `enclosing_range` "for call hierarchies" | Keep relation kind and certainty separate from distance; witnesses are occurrence ranges inside an enclosing entity |
| GitNexus `impact` | Groups by depth as "WILL BREAK" / "LIKELY AFFECTED", with a confidence %. Issue #414: unbounded `byDepth` output (140 KB+), fixed by `limit`/`offset`/`summaryOnly` | Its labels conflate hop distance with breakage, which this design avoids. Output must be bounded per call |
| CodeGraph | A single `explore` tool. `callers`/`callees`/`impact` are hidden by default because explore inlines them | One tool beats many tools, consistent with AGC v4's single `oxide_search` shape |
| CodeScaleBench | Paired runs with and without the tool (3+ repetitions); curated oracles including dependency chains; IR metrics; "MCP death spiral" under prescriptive preambles; small mean reward delta (+0.035) | Matches AGC v3 G vs D: guidance raises use, not outcomes. Measure paired against a capable local baseline |

## Audit at HEAD: what OXIDE already has

Unchanged from graph-navigation's table, with one addition:

| Need | Where | Status |
| --- | --- | --- |
| Exact target | `Impact.root`, `roots` | Yes. Resolving a *name* to a key is not public (`search` returns candidates) |
| Hop distance | `ImpactHop.depth` | Yes |
| Certainty | `ImpactHop.proven_depth`; `Relation.resolution` / `basis` | Yes, as separate fields. Dispatch edges are `basis="heuristic"`, so never proven |
| Relationship path | `ImpactHop.via` (one shallowest step); **new:** `navigation.Walk.graph`, the full walked subgraph (ADR-0012) | Available internally, not in `Impact` |
| Source witness | `Relation.evidence` via `relations(key)` | Yes; not attached to hops (one extra read per hop) |
| Ambiguity | Ambiguous edges set `depth`, never `proven_depth` | Yes |
| Completeness | `stopped_by`; `unresolved` (by name) | Partial. Sensitivity 0.73. The eval-only *residual* (name occurrences no edge explains) reached 11/11 |
| Change impact | None: OXIDE makes no breakage claim | Correct; keep it that way |

**Parity (measured).** [scripts/score.py](scripts/score.py) re-scores graph-navigation's
impact arm against its frozen truth on a store indexed at HEAD. The results are identical
to the frozen report ([results/score-base.txt](results/score-base.txt)):

| | Q1 recall | Q1 F1 | Q2 recall | Q2 F1 |
| --- | --- | --- | --- | --- |
| Frozen report (5ed725d) | 0.659 | 0.701 | 0.585 | 0.633 |
| HEAD | 0.659 | 0.701 | 0.585 | 0.633 |

The control results therefore still stand:
- grep: Q1 recall 1.0, F1 0.828, 110 false claims;
- search/context: 41% of direct dependents, about 2.8k tokens;
- impact: about 3% of grep's Q2 tokens, 1 call against a median of 6;
- latency: rg 8 ms, impact 11–13 ms, `build_context` 88 ms.

## Agent evidence so far (one client, OpenCode gpt-6-luna)

| Study | Tasks | Natural use of the graph | Outcome |
| --- | --- | --- | --- |
| AGC v1, v2 | Easy for grep (baseline 94%) | 5/24 runs or fewer | REJECT: unused, cost without gain |
| AGC v3 | Dispatch and two-hop impact (baseline 57%) | 67% unguided | Forced use: success 0.86 vs 0.57, false claims 0.67 vs 2.05, CPS 0.58×. PROMOTE G (prereg), though guidance made G worse than D |
| AGC v4 | Fresh, mixed (baseline 6/8) | 2/6 structural runs | STOP: no gain, 1.14–2.26× CPS |

*Inferred:* the graph pays only where grep fails, which is dispatch and multi-hop
questions. There the remaining risk is silent incompleteness.

## New lead: the global `attrs` block in method dispatch

AGC Part 1 noted that impact upstream of `requests` `Session.request` returns nothing,
although `Session.get`…`delete` all call `self.request(...)`.

**Root cause (measured).**
- `python_dispatch.Dispatch.method` gives up when the method name is assigned as an
  attribute *anywhere* in the capture (`x.m = ...`, `Parsed.attrs`).
- `requests/adapters.py:288 response.request = req` therefore blocks every
  `self.request()`.
- This is the documented rule. `test_dispatch.py:126` pins its intended case:
  `self.handler = print` in the same class.

**Upper bound.** [scripts/attrs_probe.py](scripts/attrs_probe.py) re-indexes with the
block disabled for this process only →
[results/attrs_gain.json](results/attrs_gain.json):

| Corpus | Call edges, base → no block | Lost | Gained |
| --- | --- | --- | --- |
| flask | 997 → 1,026 | 0 | 29 (`@app.route`/`errorhandler`/`register_blueprint` on test apps, `Flask.wsgi_app`, …) |
| requests | 877 → 889 | 0 | 12 (7 × `Session.request` from `Session.get`…`delete`, `build_response`) |
| pytest | 4,486 → 4,527 | 0 | 41 (`TerminalWriter.write`, `super().__init__`, `CaptureIO.getvalue`, …) |

- **Correctness:** all 82 gained edges were read against the assignments that blocked
  them. Each names the method that runs by default. The blocking assignment is on an
  unrelated class or object, or is a runtime monkeypatch (`app.wsgi_app = Middleware(...)`,
  `tr._tw.write = ...`) of the kind the closed world already excludes for `setattr`.
- **Labelling:** all are `basis="heuristic"`, like every dispatch edge. They are never
  proven, and the router doesn't follow them.
- **Effect on the frozen truth:** Q1 is unchanged and Q2 F1 moves from 0.633 to 0.639
  ([results/score-noattrs.txt](results/score-noattrs.txt)). The frozen misses are 36
  attribute calls on receivers of unknown type (`got_request_exception.send`), which this
  rule doesn't touch.

**The fix (implemented afterwards, see § Implementation).**
- **Rule:** a name blocks dispatch only if it is assigned on the receiver (`self.m = ...`,
  `cls.m = ...`) in a method of a class on the looked-up hierarchy's MROs. Assignments on
  any other receiver no longer block. This keeps `test_dispatch.py:126`.
- **Edges recovered:** estimated at about 81 of the 82, which was wrong for flask. The
  measured count is 53 (see § Implementation).
- **Cost:**
  - one per-scope field in the Python parse facts (`FACTS_VERSION` v3 → v4);
  - a few lines in `Dispatch.method`;
  - `oxide-python-v5` → v6;
  - identity-only amendments of the frozen baselines, as graph-v2 did with
    `baseline-v13` / `typescript-v10`. The router ignores heuristic edges, so *inferred*:
    identity only.

## What would justify an agent-facing representation

Nothing new beyond graph-navigation's list. The binding gap is calls through receivers
of unknown type, 36 of the 44 frozen misses, which needs type inference OXIDE does not
do. Until completeness is fixed, the smallest honest representation is the one the AGC
tools used:
- target;
- hops, each with its distance, `resolved` or `likely`, and a `path:line` witness;
- `stopped_by`;
- the residual of unexplained name occurrences;
- a not-exhaustive note.

It makes no breakage claims, is bounded per call, and is never merged into context. It
stays in eval scripts.

## Reproduce

```sh
S=docs/phase-6/graph-navigation-v2/scripts
for c in flask requests pytest; do for m in base noattrs; do
  uv run python $S/attrs_probe.py $m $c; done; done      # base = HEAD code at the time
git worktree add --detach /path/to/head-wt b7f7959       # HEAD for the acceptance checks
for c in xarray seaborn; do
  PYTHONPATH=/path/to/head-wt/src uv run python $S/attrs_probe.py head $c
  uv run python $S/attrs_probe.py fix $c; done
uv run python $S/index_time.py /path/to/head-wt/src src
uv run python $S/score.py ~/Projects/oxide-eval-data/oxide-gnav2-eval/data-base
uv run python $S/score.py ~/Projects/oxide-eval-data/oxide-gnav2-eval/data-noattrs
```

## Not tested

- New agent runs: the task sets are spent, and the evidence above doesn't call for one.
- TypeScript, Go and Rust dispatch.
- The narrow rule itself: its gain is inferred from the upper bound.

## Implementation (narrow rule, uncommitted)

**Code.**
- `python.py`: per-scope `assigned` facts, `(receiver, name)` for `receiver.name = ...` /
  `del` on a plain name. `FACTS_VERSION` v3 → v4; `VERSION` `oxide-python-v5` → v6.
- `python_dispatch.py`:
  - `Dispatch.assigned()` types each receiver the way a call receiver is typed: `self` /
    `cls` (also inside closures), a name bound once by constructing an in-repo class, or
    a class name.
  - `method` gives up when the name is assigned on any class in a candidate's MRO.
  - Untyped receivers no longer block. This is the accepted trade: `requests/auth.py`
    `_r.request = prep` would otherwise keep blocking `Session.request`.
- `Parsed.attrs` (global) is kept for its other use, the module-global rebinding guard
  in `_instance`.

**Regression tests.** `test_dispatch.py::test_attributes_hide_methods_only_on_their_own_class_hierarchy`:
- the requests case resolves, and an unrelated class with an assigned attribute of the
  same name does not block it;
- real shadowing stays unresolved: an attribute set by a base-class method, one on a
  constructed instance (`c = Client(); c.send = print`), and a class attribute
  (`Patched.run = print`);
- the existing `self.handler` case (`:126`) still holds.

Mutation check: HEAD's `Dispatch` fails the requests assertion, and removing the MRO
check fails the inheritance assertion.

**Edges against HEAD (measured)** → [results/fix_gain.json](results/fix_gain.json):

| Corpus | HEAD → fix | Added | Removed |
| --- | --- | --- | --- |
| flask | 997 → 998 | 1 (`Flask.__call__` → `Flask.wsgi_app`) | 0 |
| requests | 877 → 889 | 12 (7 × `Session.request`, …) | 0 |
| pytest | 4,486 → 4,526 | 40 (38 resolved, 2 ambiguous) | 0 |

- **All 53 added edges are a subset of the 82 audited above, so 0 are incorrect.** The
  flask edge is the default target; flask's `app.wsgi_app = Middleware(...)` pattern can
  replace it at run time.
- **The upper bound was misattributed:**
  - the other 28 flask edges (`@app.route` and similar, on module-level
    `app = Flask(...)`) came from a *different* guard, which the probe also disabled: a
    module-level constructed name that is also assigned as an attribute somewhere
    (`self.app = app`) may be rebound from outside;
  - that guard is kept;
  - pytest's `_NodeReporter.to_xml` stays blocked, because its own class reassigns
    `self.to_xml`.
- **Frozen graph-navigation truth** ([results/score-fix.txt](results/score-fix.txt)):
  - Q1 is identical on all 24 targets (recall 0.659, F1 0.701);
  - Q2 recall goes from 0.585 to 0.597 and F1 from 0.633 to 0.639, with precision
    unchanged.

**Verification.** `mise run verify`: lint and mypy pass; 383 tests pass. The one failure
is `test_contextbench.py::test_output_is_compatible`, which reports only the derivation
id change; `test_quality_does_not_regress` passes. The TypeScript gate
(`OXIDE_CONTEXTBENCH_TS=1`) has the same single identity failure and 16 passes.
The identity-only freezes are recorded separately in
[../baseline-v14/README.md](../baseline-v14/README.md) and
[../typescript-v11/README.md](../typescript-v11/README.md).

## Acceptance checks (maintainer ACCEPT, 2026-10-25)

**xarray and seaborn against HEAD** (HEAD from a `git worktree` of b7f7959 on
`PYTHONPATH`, see [results/fix_gain.json](results/fix_gain.json)):

| Corpus | HEAD → fix | Added | Removed |
| --- | --- | --- | --- |
| xarray | 11,048 → 11,050 | 2 | 0 |
| seaborn | 3,526 → 3,545 | 19 | 0 |

The parser's `assigned` facts show what blocked each name at HEAD. All 21 edges are
correct on reading:

| What blocked it at HEAD | Edges | Why the edge is right |
| --- | --- | --- |
| `self.X =` in an unrelated class: `_RefCounter._counts` (file_manager), categorical's `self.join`, matrix/regression's `self.label` | 13 | A different hierarchy from `DataArrayRolling`, `PlotData` and `Continuous` |
| Unknown type: `result.index =` (groupby) and `df.index =` (test), both pandas objects | 1: `StringAccessor.rindex` → `StringAccessor.index` | The receivers are pandas Series and DataFrames |
| Unknown type: `cls.map = classmethod(cls.map)` in `_decorators.share_init_params_with_map`, a function parameter | 6: `FacetGrid.map` ×3, `SemanticMapping.map` ×3 (`HueMapping.map(...)` and similar) | `FacetGrid` is not decorated. The decorated mappings' `map` wraps the inherited `SemanticMapping.map` function, so that body still runs |
| Dotted receiver on an external class: `mpl.colors.Colormap._repr_png_ =` | 1: `Plot._repr_png_` | matplotlib's `Colormap` is not `Plot` |

- 8 of the 21 edges exist only because receivers of unknown type no longer block, and
  all 8 are correct.
- Across all five corpora: 74 edges added, 0 removed, 0 found incorrect.

**Index time and peak RSS** ([scripts/index_time.py](scripts/index_time.py) →
[results/index_time.json](results/index_time.json)). Setup:
- fresh interpreters and fresh stores;
- HEAD and fix interleaved, alternating order;
- 5 repetitions per repository;
- 200% CPU cap, the same machine and session.

| Repo | HEAD median s | Fix median s | Δ | Δ peak RSS |
| --- | --- | --- | --- | --- |
| flask | 0.675 | 0.678 | +0.4% | +0.5 MB |
| requests | 0.496 | 0.519 | +4.8% | +0.4 MB |
| pytest | 1.510 | 1.584 | +4.9% (+74 ms) | +2.4 MB |
| xarray | 1.759 | 1.762 | +0.2% | +1.3 MB |
| seaborn | 1.331 | 1.333 | +0.2% | −0.4 MB |

- Run-to-run spread within a tree is about ±5%, so requests is within noise.
- pytest's +74 ms is consistent across repetitions. It is the pre-pass typing each
  attribute assignment once (*inferred*).
- No regression is meaningful at this size.
