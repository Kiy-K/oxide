# graph-v1: graph intelligence and algorithmic navigation (2026-10-23)

Question: where is OXIDE's relation graph wrong or thin, which graph algorithms earn a
place, and what bounded navigation helps a coding agent? Numbers are measured unless
marked *inferred*. Held-out tasks were opened only by the standard baseline gate; the
algorithm studies used dev splits only ([preregistration.md](preregistration.md),
frozen at `984dcab` before any treatment arm ran).

Outcome:

| Change | Verdict | Where |
| --- | --- | --- |
| Python cross-file resolution (`oxide-python-v4`): nested import roots, re-exports, module/class attributes | **PROMOTE** (graph correctness; context quality unchanged) | `core/languages/python_resolve.py`, [baseline-v12](../baseline-v12/README.md), [typescript-v9](../typescript-v9/README.md) |
| `Repository.impact`: bounded upstream/downstream navigation with proven/unproven depths, cycle flags, unresolved-by-name count | **SHIP** as a Python API; the MCP tool is unchanged | `core/navigation.py`, [public-api § Impact](../../public-api.md) |
| LadybugDB native `algo` extension (PageRank, SCC, Louvain, k-core, WCC) | **NOT VIABLE** at the pin: no published build loads | [§ LadybugDB algo extension](#ladybugdb-algo-extension) |
| Personalized PageRank value for routed-only symbols | **REJECT**: recall identical in every arm, more routed symbols only add noise | [§ Study 1](#study-1-personalized-pagerank) |
| Louvain communities as regions | **REJECT**: +0.06 share over directories, below the +0.10 gate | [§ Study 2](#study-2-communities) |
| Strongly connected components | **USED** only as `impact`'s cycle flag; no ranking role | [§ Audit](#audit) |

## Audit

`scripts/graph_audit.py` indexed nine repositories (OXIDE `src`, flask, requests, xarray,
pytest, darkreader, code-server, gh-cli, ripgrep) at `oxide-python-v3` and tallied
relations by kind × resolution × basis, unresolved call shapes, and SCCs of the resolved
call graph and of the file dependency graph (`audit/python-v3.json`).

| Corpus | Files | Entities | Relations | Calls resolved | Calls ambiguous | Imports resolved | `tested_by` | Call SCCs (largest) | File SCCs (largest) | Index s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |
| OXIDE src | 118 | 545 | 3,804 | 27% | 0.0% | 49% | 0 | 0 | 0 | 0.51 |
| flask | 80 | 1,731 | 6,940 | 5% | 0.0% | 17% | 77 | 0 | 1 (15) | 0.46 |
| requests | 35 | 780 | 4,116 | 15% | 0.7% | 37% | 179 | 0 | 0 | 0.31 |
| xarray | 173 | 6,728 | 58,321 | 20% | 0.6% | 41% | 4,988 | 2 (2) | 1 (43) | 2.35 |
| pytest | 245 | 5,884 | 29,864 | 7% | 0.1% | 2% | 489 | 3 (3) | 1 (2) | 1.46 |
| darkreader (TS) | 210 | 1,598 | 10,754 | 31% | 1.4% | 85% | 0 | 1 (2) | 2 (6) | 0.65 |
| code-server (TS) | 83 | 569 | 5,274 | 22% | 0.0% | 62% | 455 | 0 | 2 (4) | 0.34 |
| gh-cli (Go) | 733 | 6,855 | 72,483 | 28% | 0.0% | 41% | 6,677 | 0 | 5 (3) | 3.05 |
| ripgrep (Rust) | 236 | 4,292 | 24,707 | 47% | 1.6% | 58% | 1,868 | 3 (4) | 5 (10) | 1.32 |

What it showed:

- **Provenance is already typed.** Every relation carries `resolution` (resolved /
  ambiguous / unresolved) and `basis` (syntactic / resolved / heuristic); unresolved
  targets are `Opaque` rows with the name used. The gap was not edge typing but
  consumers: `View.adjacency` dropped both fields, so routing and any navigation saw
  ambiguous and heuristic edges as facts.
- **Python resolution was the weakest slice**: 5–27% of calls resolved, against 22–31%
  for TypeScript, 28% for Go and 47% for Rust, and 2% of imports on pytest. The most frequent
  "unresolved" bare calls were in-repo classes: `DataArray` 1,048×, `Dataset` 915×,
  `Variable` 429× in xarray; `Flask` 39× in flask. A minimal fixture reproduced three
  causes (below). Builtins (`len`, `isinstance`) and instance method calls (`self.x()`,
  2,255 in xarray) are the remainder.
- **Unresolved calls are mostly qualified** (`a.b()`, `pkg.F()`): 48–71% in Python,
  49–52% in TypeScript, 89% in Go; Rust's unresolved calls are mostly bare (20%
  qualified: macros, `Ok`, `Some`).
- **SCCs are tiny.** Resolved call-graph cycles have at most 4 symbols; file dependency
  cycles are small except xarray's 43-file core. Cycle structure carries little ranking
  signal; it is reported, not used to rank (*inferred* from the sizes).
- Ambiguity is rare (≤ 1.6% of calls) and is overload ordinals or declaration merging,
  not competing definitions.

## Python cross-file resolution (`oxide-python-v4`)

Python scoping only, no type inference, nothing invented (SPEC § Relations):

1. **Nested import roots.** A file inside a package whose import root is a nested
   directory (the first ancestor without `__init__.py`, which pytest's `prepend` mode
   puts on `sys.path`) also answers to its root-relative name, so `src/flask/app.py` is
   `flask.app`. A repository-relative name wins when both exist, and a submodule is
   looked up under the name its parent resolved to. Files outside a package get no
   second name. Module identities are unchanged.
2. **Re-exports.** A name a module binds by import resolves through that import, so
   `from pkg import Client` reaches `pkg/core.py#Client` via `pkg/__init__.py`. Cycles
   resolve nothing (a guard returns no evidence); star re-exports stay unresolved.
3. **Attributes of a module or class.** `util.helper()`, `pkg.util.helper()`,
   `Class.method()` resolve when the head binds exactly one in-repo module or class.
   Instance and inherited attributes stay unresolved (dynamic dispatch).

Effect on resolved relations (`audit/python-v4.json`; `scripts/edge_sets.py`,
`scripts/edge_diff.py`):

| Corpus | Calls resolved v3 → v4 | Imports resolved | Resolved edges lost | New resolved edges | Index s |
| --- | --- | --- | ---: | ---: | --- |
| OXIDE src | 27% → 28% | 196 → 196 | 0 | — | 0.51 → 0.47 |
| flask | 5% → 17% | 105 → 217 | 0 | 617 | 0.46 → 0.49 |
| requests | 15% → 28% | 178 → 181 | 0 | 297 | 0.31 → 0.33 |
| xarray | 20% → 33% | 1,386 → 1,490 | 0 | 5,863 | 2.35 → 2.50 |
| pytest | 7% → 14% | 44 → 1,088 | 0 | 2,582 | 1.46 → 1.54 |

- **Precision.** A hand review of 48 randomly sampled new edges (seed 20261023,
  12 per corpus) found 48 correct (`flask.abort(400)` → `helpers.abort`,
  `requests.Session()` → `sessions.Session`, `Dataset(...)` → `core/dataset.Dataset`, a
  classmethod via `Server.basic_response_server`). Independent oracle ([results/jedi-summary.md](results/jedi-summary.md)): of 2,337 new
  edges on flask, requests and pytest, jedi agrees with 2,241, finds nothing for 92 and
  disagrees on 4. All 4 are jedi errors (it resolves a `@classmethod` to
  `builtins.classmethod`), so precision is 0.998 by jedi and 1.000 after inspection.
- **Ambiguity** grows (pytest 15 → 1,376 rows, xarray 238 → 945) only because newly
  reachable targets are `@overload` stubs: `pytest.raises` → `raises`, `raises~1`,
  `raises~2`. That is the existing overload policy (ADR-0010 ordinals), not a new guess.
- **`tested_by`** roughly doubles (xarray 4,988 → 9,905) because more test calls resolve;
  it stays `basis=heuristic`.
- **Cost**: index time +2–7%; facts are unchanged, so caches are reused.

Context quality ([baseline-v12](../baseline-v12/README.md), all 63 Python tasks):
routed coverage 0.673 → 0.730 (verified) and 0.575 → 0.811 (unverified);
`file_recall@4096` 0.693 → 0.716; packed line and span recall unchanged; noise
(`gold_unit_share`) unchanged. TypeScript ([typescript-v9](../typescript-v9/README.md)):
identical except the derivation id. The new reach is lost at selection, which values
routed depth-2 symbols below the floor (router-reachability, again).

## Bounded navigation: `Repository.impact`

Modelled on GitNexus's upstream/downstream impact with depth grouping and confidence,
but with OXIDE's provenance instead of scores
([public-api § Impact](../../public-api.md)):

- level-synchronous BFS in domain order with a visited set, run twice: over every
  followed edge (`depth`) and over proven edges only (`proven_depth`; `resolved` with a
  non-heuristic basis). Ambiguous candidates and `tested_by` never prove a path.
- the root stands for its physical descendants (a class's methods) and, for a file, its
  logical modules (imports name modules).
- `cyclic`: strongly connected components (iterative Tarjan) of the walked subgraph.
- `unresolved`: unresolved sites whose text matches a root's name, a by-name signal that
  the walk may be incomplete.
- bounds: depth ≤ 8, ≤ 1,000 entities, 20,000 adjacency rows per pass; `stopped_by`.

`View.adjacency` now returns `(kind, other, proven)`. The router ignores the flag, so
routing is byte-identical (baseline B arm reproduced baseline-v12 dev exactly).

Cost and shape, 40 random non-test symbols per corpus, default depth 3
(`scripts/impact_perf.py`, `audit/impact-perf.jsonl`):

| Corpus | Upstream p50 / p95 ms | Downstream p50 / p95 ms | Upstream empty | Proven share (upstream) |
| --- | --- | --- | ---: | ---: |
| requests | 4.5 / 8.1 | 2.7 / 5.9 | 25/40 | 0.97 |
| xarray | 25.8 / 38.3 | 10.5 / 36.9 | 32/40 | 0.89 |
| pytest | 18.9 / 29.3 | 6.9 / 14.7 | 29/40 | 1.00 |
| ripgrep | 12.8 / 23.6 | 9.6 / 16.2 | 22/40 | 1.00 |
| gh-cli | 34.9 / 41.9 | 7.5 / 21.3 | 15/40 | 1.00 |
| VS Code | 326.6 / 435.5 | 58.4 / 149.2 | 37/40 | 0.67 |

The `unresolved` count's share of that cost was not isolated; the VS Code upstream
time includes it.

Most random symbols have no resolved dependents: they are methods reached only through
instances (`obj.m()`), which OXIDE does not resolve. `unresolved` makes that visible
(VS Code median 4 by-name sites per root) instead of reporting a safe empty set.

Not exposed through MCP. Prior evidence: agents never called navigation tools in the
2026-10-12 navigation experiment, and structural search showed no benefit over grep
(`docs/phase-6/structural-mcp-trial/`). Exposure needs its own preregistered agent
trial on tasks where impact is the bottleneck.

## LadybugDB algo extension

LadybugDB documents an `algo` extension (PageRank, Kosaraju/parallel SCC, WCC, k-core,
Louvain, run on `PROJECT_GRAPH` projections with relationship-property filters). It is
not bundled in the `ladybug==0.21.0` wheel (`LOAD algo`: "official extension and has
not been installed"). Builds are published for all four pinned platforms; their
SHA-256 are pinned in `scripts/algo_probe.py`, which verifies before loading:

| Platform | SHA-256 | Bytes | Links against |
| --- | --- | ---: | --- |
| linux_amd64 | `6bc1e3dc…1cca` | 1,242,408 | `libnetworkit.so`, `libarrow.so.2500`, `libgomp` |
| osx_arm64 | `77b891d7…cca7` | 1,243,432 | `libnetworkit.dylib`, `libarrow.2500.dylib`, `libomp` |
| osx_amd64 | `e4deec75…cb4c` | 486,880 | (not inspected) |
| win_amd64 | `270777b1…3c3f` | 16,376,832 | `networkit_state.dll`, `arrow.dll`, OpenSSL 3 |

The networkit and arrow libraries are not published next to it (HTTP 404), so on Linux
`LOAD EXTENSION` fails ("libnetworkit.so: cannot open shared object file"); the
digest-verified probe reproduces it. Even loaded, PageRank there is global, not
personalized, and Louvain is parallel (Grappolo), so determinism would need its own
proof. Graph algorithms that earn a place run in Python over bounded, query-local
subgraphs (≤ 64 routed regions), where cost is small and order is domain-defined.

## Study 1: Personalized PageRank

Preregistered arms on dev only (`results/ppr-*.metrics.json`, `results/ppr-gate.txt`).
B for Python is the frozen baseline-v12 dev rows (the B smoke run reproduced them
byte-for-byte); TypeScript B was re-run and equals typescript-v9 dev exactly.

| Dev, verified gold | Python B / D / P (15 tasks) | TypeScript B / D / P (25 tasks) |
| --- | --- | --- |
| `line_recall@256` | 0.180 / 0.180 / 0.180 | 0.195 / 0.195 / 0.195 |
| `line_recall@1024` | 0.548 / 0.548 / 0.548 | 0.330 / 0.330 / 0.330 |
| `line_recall@4096` | 0.659 / 0.659 / 0.659 | 0.370 / 0.370 / 0.370 |
| `span_recall@4096` | 0.623 / 0.623 / 0.623 | 0.358 / 0.358 / 0.358 |
| `gold_unit_share@4096` | 0.189 / 0.144 / 0.139 | 0.177 / 0.152 / 0.153 |

No task's `line_recall@4096` differs between arms. P − D = 0.000 < +0.02 in both
languages: **REJECT** (gate: "REJECT PPR if P − D < +0.02 in either language"). D − B,
reported as evidence about the floor: admitting the top half of routed-only symbols adds
no gold lines and lowers `gold_unit_share@4096` by 0.045 (Python) and 0.025
(TypeScript). The routed symbols below `INCLUDE_FLOOR` are not where the missing gold
is, so the floor stays. Cost: P's wall time equals B's within noise (393 s vs 397 s for
TypeScript dev). The preregistration said 26 TypeScript verified tasks; one of the 26
dev tasks has no verified gold, so 25 are scored.

## Study 2: communities

`results/community-{py,ts}.json`, Louvain over the resolved file graph, anchor = top-1
search hit's file:

| Dev | Tasks | Community share | Directory share | Mean community / directory files |
| --- | ---: | ---: | ---: | --- |
| Python | 15 | 0.700 | 0.644 | 8.6 / 12.4 |
| TypeScript | 26 | 0.468 | 0.404 | 146.3 / 9.3 |

Community − directory share is +0.056 (Python) and +0.064 (TypeScript), below the +0.10
gate, and TypeScript communities are 16× larger than directories: **REJECT** for this
release; nothing in `src/`.

## Rejected or not attempted

- **`self.method()` resolution** (2,255 sites in xarray). Dynamic dispatch: a subclass
  may override. It would be a `heuristic` edge that routing follows like a fact unless
  routing also changes, so it needs its own derivation and evaluation. Not done here.
- **Edge weighting / confidence scores** (GitNexus-style). OXIDE's categorical
  provenance already separates proven from uncertain; a numeric confidence would be an
  uncalibrated score (SPEC § Storage: scores are not probabilities).
- **Native graph algorithms in LadybugDB**: see above.

## Remaining limitations

- Python: instance and inherited attributes, star re-exports, `__all__`, dynamic
  imports and `sys.path` manipulation stay unresolved; a nested import root is inferred
  from `__init__.py` files, not from packaging metadata.
- `impact` inherits every resolution gap; `unresolved` is a by-name hint, not a bound.
- Ambiguous overload targets (`raises`, `raises~1`, …) are reported as separate entities.

## Reproduce

    uv run python docs/phase-6/graph-v1/scripts/graph_audit.py OUT.json REPO...
    uv run python docs/phase-6/graph-v1/scripts/edge_sets.py x OUT.json REPO...   # per version
    uv run python docs/phase-6/graph-v1/scripts/edge_diff.py AFTER.json BEFORE.json
    uv run python docs/phase-6/graph-v1/scripts/impact_perf.py REPO...
    uv run python docs/phase-6/graph-v1/scripts/algo_probe.py DIR
    uv run python docs/phase-6/graph-v1/scripts/jedi_extract.py EDGES.json REPO...    # per version
    JEDI_VENV/bin/python -I docs/phase-6/graph-v1/scripts/jedi_judge.py EDGES.json ROWS.json
    uv run python docs/phase-6/graph-v1/scripts/ppr_run.py {py|ts} {B|D|P} OUT
    uv run --with networkx==3.5 python docs/phase-6/graph-v1/scripts/community_probe.py {py|ts} OUT.json
