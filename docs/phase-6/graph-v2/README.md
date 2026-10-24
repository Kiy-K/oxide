# graph-v2: receiver-aware Python resolution, impact cost, graph-to-context (2026-10-24)

Questions: can Python instance/inherited method calls resolve without losing edge
precision; can `Repository.impact` stop scanning every unresolved row; and where does gold
that the graph newly reaches get lost before it is packed? Numbers are measured unless
marked *inferred*. Held-out tasks were opened only by the standard baseline gates; the
routing study used Python dev only ([preregistration.md](preregistration.md), frozen
before any arm ran).

Outcome:

| Change | Verdict | Where |
| --- | --- | --- |
| Receiver-aware dispatch (`oxide-python-v5`): `self.m`, `cls.m`, `super().m`, inherited `Class.m`, `x = C(); x.m()` through a C3 MRO, as `basis="heuristic"` edges | **PROMOTE** (graph correctness; context quality unchanged) | `core/languages/python_dispatch.py`, `python_resolve.py`, [baseline-v13](../baseline-v13/README.md), [typescript-v10](../typescript-v10/README.md) |
| `Unresolved` nodes keyed by the name's last segment (`oxide-ladybug-py-v2`) so `impact`'s unresolved count is an index lookup | **SHIP**: VS Code `impact` p50 543 → 101 ms, counts identical | `storage/ladybug.py` `unresolved_key`, `View.mentions` |
| Router follows dispatch edges | **REJECT** (preregistered, dev): recall identical at every budget, one gold span displaced | [§ Routing over dispatch edges](#routing-over-dispatch-edges) |
| Graph-to-context bottleneck | **Selection policy, not discovery**: every span graph-v1 newly routed is lost at the selection floor or the budget | [§ Where reached gold is lost](#where-reached-gold-is-lost) |

## What resolves now

`python_dispatch.py` computes each in-repo class's C3 MRO, with a `?` token for a base it
cannot see (external, computed, or a name that is not one in-repo class). Lookup walks the
MRO and gives up at the first `?`, so a method an external base might define is never
claimed. It also gives up on classes with a metaclass, keyword or splat base, classes
defining `__getattribute__`, and names assigned as attributes anywhere in the repository
(`obj.m = ...`). Private `__m` names resolve only in their own class body (mangling). A
base cycle resolves nothing. Code outside the capture (subclasses elsewhere,
monkey-patching through `setattr`) is not seen, which is why these edges are heuristic.

| Site | Resolves to | Resolution |
| --- | --- | --- |
| `self.m()` / `cls.m()` in a method of `C` (receiver = first parameter, bound once, not a `staticmethod`) | every definition of `m` that `C` or an in-repo subclass dispatches to | `resolved` if one, `ambiguous` if several |
| `super().m()` (zero-argument) in `C` | the definition after `C` in the MRO of `C` and of each subclass | same |
| `Class.m` when `Class`'s body has no `m` | the MRO's definition | same |
| `x = C(...)` (bound once in the scope), then `x.m()` | `C`'s own MRO only (exact type known); an in-repo `__new__` voids it | `resolved` |

All of these carry `basis="heuristic"` (the closed-world hierarchy is an assumption), create
no `tested_by` edges, and are not walked by the router (`routing.follows`).
`Repository.impact` counts them as reachable but never proven.

### Precision and coverage

Every edge from v4 was compared with v5 by (source, kind, target, evidence)
(`scripts/edge_dump.py`, `scripts/edge_diff.py`, [results/edge-diff-v4-v5.txt](results/edge-diff-v4-v5.txt)):

| Corpus | v4 edges lost / changed | New resolved | New ambiguous | New sites | Sites with 1 / 2–5 / 6+ candidates | Index s (v4 → v5) | Peak RSS MB |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| flask | 0 / 0 | 324 | 29 | 333 | 324 / 8 / 1 | 0.91 → 0.93 | 149 → 151 |
| requests | 0 / 0 | 202 | 4 | 204 | 202 / 2 / 0 | 0.65 → 0.68 | 146 → 147 |
| xarray | 0 / 0 | 1,470 | 624 | 1,604 | 1,470 / 67 / 67 | 2.59 → 2.89 | 182 → 188 |
| pytest | 0 / 0 | 928 | 56 | 943 | 928 / 14 / 1 | 2.20 → 2.46 | 179 → 185 |

Unresolved `self.m` calls ([audit/shapes-v4.json](audit/shapes-v4.json) →
[audit/shapes-v5.json](audit/shapes-v5.json)): flask 143 → 30, requests 82 → 16, xarray
945 → 226, pytest 682 → 79. Most of the rest are calls through an attribute
(`self.options.get`) or into an external base.

Oracle: jedi 0.20.1 (scratch venv, `goto(follow_imports=True)`) on all 3,084 new sites
(`scripts/jedi_dispatch.py`, [results/jedi-sites-v5.txt](results/jedi-sites-v5.txt),
disagreements and misses in [results/jedi-v5-not-agree.json](results/jedi-v5-not-agree.json)):

- **Resolved sites:** 2,894 of 2,924 agree, and jedi finds nothing for 29. Those 29 are
  xarray tests' `ds = Dataset(...); ds.dropna()` and pytest `_io`'s
  `tw = TerminalWriter(); tw.line()`, all correct by inspection. One site disagrees:
  pytest `Module.collect`'s `super().collect()`. OXIDE gives `PyCollector.collect`, jedi
  gives `Collector.collect`. Python's actual MRO for `Module` (`Module, File,
  FSCollector, PyCollector, PyobjMixin, Collector, …`, checked by simulating the class
  statements) puts `PyCollector` first, so jedi is wrong. **Precision: 2,895 / 2,895
  judged.**
- **Ambiguous sites:** in all 160 (flask 9, requests 2, xarray 134, pytest 15), the
  candidate set contains jedi's answer. jedi reports the static class of `self`, and
  OXIDE's set adds the overrides in subclasses.

Cost: v5 adds 2–12% to index time on these four corpora (single runs) and 1–6 MB peak
RSS. No v4 edge changed, so retrieval, routing and packing outputs are byte-identical
(baseline-v13 = baseline-v12 except the derivation id).

## Impact query cost

graph-v1 measured `impact` dominated by `View.mentions`. That function counts unresolved
call/reference sites whose name may target the queried entities, by scanning every
`Opaque` row. v1 stores keep one `Unresolved` node per site. v2 stores keep one node per
last name segment (`unresolved_key`: `a.b.low`, `low` and `mod::low` all become `u:low`).
`mentions` then matches `u.key IN $keys` (a primary-key lookup) before applying the
unchanged name predicate. A name with unusual separators, such as `a:::low`, falls back to
the full scan. A test checks that every name the predicate can match shares the key
(`test_impact.py::test_unresolved_sites_share_the_key_of_every_name_they_may_target`). An
`UNWIND`-per-key form was measured slower than the full scan (17 vs 15 ms on xarray), so
it was dropped.

`scripts/mentions_v2.py`, [results/mentions-v2.jsonl](results/mentions-v2.jsonl): for 40
sampled entities per corpus, v1 store with v4 code vs v2 store with v5 code. The
unresolved counts are equal on 40/40 for every corpus.

| Corpus | Unresolved nodes (v1 → v2) | `mentions` p50 ms | `impact` upstream p50 / p95 ms | Store MB | Index s |
| --- | --- | --- | --- | --- | --- |
| requests | 2,044 → 367 | 2.7 → 0.5 | 7.8 / 14.9 → 6.2 / 12.8 | 15.0 → 14.8 | 0.7 → 0.7 |
| xarray | 16,942 → 1,065 | 20.5 → 0.6 | 33.2 / 61.4 → 16.9 / 41.8 | 25.1 → 25.4 | 2.6 → 2.7 |
| pytest | 15,449 → 1,120 | 18.5 → 0.6 | 29.6 / 41.8 → 10.7 / 26.6 | 25.3 → 25.8 | 2.2 → 2.5 |
| gh-cli (Go) | 32,843 → 1,565 | 38.7 → 0.7 | 60.9 / 77.5 → 26.2 / 36.7 | 35.3 → 32.8 | 4.8 → 4.5 |
| ripgrep (Rust) | 5,539 → 337 | 6.8 → 0.5 | 22.0 / 26.3 → 13.5 / 22.1 | 19.2 → 19.0 | 1.5 → 1.4 |
| VS Code (TS) | 376,892 → 32,729 | 437 → 3.5 | 543 / 741 → 101 / 192 | 281 → 261 | 43.5 → 42.3 |

The remaining VS Code `impact` time is the bounded BFS's adjacency queries, not the count.
Traversal bounds, provenance, ordering and the `incomplete` report are unchanged
(`test_impact.py` passes as before). graph-v1 reported 254k `Opaque` rows on VS Code, but
this run counted 376,892 at the same commit. The difference is not explained; both runs
agree that the count was the cost.

## Where reached gold is lost

`scripts/loss_trace.py baseline-v11 baseline-v12`
([results/loss-trace-v11-v12.txt](results/loss-trace-v11-v12.txt)): graph-v1 newly routed
6 verified gold spans. At 4,096 units, all 6 are lost after routing: 3 at
`selection: value below floor` (flask blueprints ×2, pytest reports) and 3 at `budget`
(xarray ×3). None is lost at discovery. Across all verified spans, baseline-v12/v13 lose
32 at `entry` and 39 at `routing` (discovery), against 27 at `selection` and 47 at
`budget`. Dev is similar: `entry` 2 + `routing` 5 against `selection` 9 + `budget` 11. Most
lost gold is reached but not delivered, and making the graph richer moves gold into that
reached-but-lost pool, not into the packed bundle. graph-v1's arm D (admitting routed-only
symbols) had already moved no recall, and this result agrees with it.

## Routing over dispatch edges

Preregistered ([preregistration.md](preregistration.md)) on Python dev (15 tasks):
arm B is the working tree; arm T has the router follow heuristic `calls`/`references`
edges under the same caps (`scripts/dispatch_arm.py`, gate `scripts/arm_gate.py`,
[results/dispatch-gate.txt](results/dispatch-gate.txt), raw `results/arm-{B,T}/`).
B reproduces baseline-v13's dev compat hashes on 15/15 tasks.

| Dev, verified | B | T | T − B |
| --- | ---: | ---: | ---: |
| `line_recall@256` | 0.180 | 0.180 | 0 |
| `line_recall@1024` | 0.548 | 0.548 | 0 |
| `line_recall@4096` (primary) | 0.659 | 0.659 | 0 |
| `span_recall@4096` | 0.623 | 0.623 | 0 |
| `gold_unit_share@4096` | 0.189 | 0.187 | −0.002 |
| `routed_coverage` | 0.950 | 0.944 | −0.006 |

**REJECT** (needed +0.02 on `line_recall@4096`). T changes routes or payloads on 14 of 15
tasks, but no gold span is newly routed. One span is lost: seaborn-3069's
`distributions.py` goes from routed (lost at `budget`) to unrouted. Every real-corpus dev
task already stops at the 64-region cap (`MAX_REGIONS`) or at depth, so dispatch
neighbours take region slots that other edges held (*inferred* from `route.stopped_by`
and region counts in the raw results). Dispatch edges stay heuristic and unrouted; they
serve `relations()` and `impact()`.

## Regression across languages

- **Python ContextBench:** baseline-v13 equals baseline-v12 except the derivation id
  (metrics and every entry/routed/payload hash identical).
- **TypeScript:** typescript-v10 equals typescript-v9 except the derivation id. Its
  `index_s` reflects a battery `powersave` run (see its README).
- **Go and Rust:** `scripts/eval_go.py context` (18 results) and `scripts/eval_rust.py
  context` (22) equal `docs/phase-6/{go,rust}-v1/context-after` except derivation ids.
  Metrics and gold audits are identical.
- **Tests:** `mise run verify` is green, including the frozen Phase 3 exact-route oracle
  (`test_baseline.py`) and `OXIDE_CONTEXTBENCH_TS=1` TypeScript gate.

## Failed hypotheses

- **"The graph is the bottleneck for delivered context."** Rejected twice now. graph-v1
  doubled resolved calls and graph-v2 added 3,084 precise dispatch sites, and packed
  recall moved by 0 both times. The losses are in selection and budget.
- **"Routing over dispatch edges reaches missing gold."** Rejected (above). At the region
  cap, more edges displace rather than add.
- **"`UNWIND` per key speeds up `mentions`."** Measured slower than the full scan, because
  the planner does not use the primary-key index under `UNWIND`.

## For graph-v3

1. **Selection, not graph.** The open problem is reached gold valued below
   `INCLUDE_FLOOR` and gold cut by the budget, not discovery. Any graph-aware change should
   target selection value or packing order. It should not be the rejected PPR, TreeSelect,
   Louvain, cap removal or routed-only admission; it needs its own preregistration.
2. **Region-cap competition.** If routing ever follows more edge kinds, the 64-region cap
   needs a priority between edge kinds (proven before heuristic), or new kinds only
   displace.
3. **Remaining unresolved Python shapes:** attribute-typed receivers (`self.options.get`,
   needs attribute type facts), parameters with annotations, and returns of known
   constructors. Each needs a jedi-checked precision audit like this one.
4. **`impact` latency on VS Code** is now the BFS adjacency (two queries per layer), not
   the count.
5. **MCP:** `impact` is still Python API only. Exposing it needs a separate agent-utility
   evaluation.

## Reproduce

Corpora: `~/.cache/oxide/contextbench/corpora/`. Use
`TMPDIR=~/Projects/oxide-eval-data/oxide-graph-eval/tmp`, capped with
`systemd-run --user --scope -p MemoryMax=3G -p CPUQuota=200%`. The v4 runs use a
`git worktree add --detach <tmp>/wt-v4 1b11ab6`.

```sh
uv run python docs/phase-6/graph-v2/scripts/call_shapes.py OUT.json REPO...
uv run python docs/phase-6/graph-v2/scripts/edge_dump.py edges-v5.json REPO...   # and from wt-v4
python docs/phase-6/graph-v2/scripts/edge_diff.py edges-v4.json edges-v5.json new-rows.json
JEDI_VENV/bin/python -I docs/phase-6/graph-v2/scripts/jedi_dispatch.py new-rows.json jedi-v5.json
uv run python docs/phase-6/graph-v2/scripts/mentions_v2.py REPO   # per corpus, from each tree
python docs/phase-6/graph-v2/scripts/loss_trace.py baseline-v11 baseline-v12
uv run python docs/phase-6/graph-v2/scripts/dispatch_arm.py B OUT_B   # then T OUT_T
python docs/phase-6/graph-v2/scripts/arm_gate.py OUT_B OUT_T
```
