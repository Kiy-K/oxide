# graph-v3: graph-to-context efficiency (2026-10-24)

Question: graph-v1 and graph-v2 made the graph reach more gold, but delivered context did
not change. Which constraint between the routed pool and the packed bundle actually stops
gold: the selection floor, the region cap, eligibility, ordering, the item cap or the
budget? Numbers are measured unless marked *inferred*. Only dev splits were run (Python
15 tasks, TypeScript 25 with verified gold). The held-out splits were not opened.

Outcome:

| Question | Answer |
| --- | --- |
| Where reached gold is lost | **Budget allocation among eligible candidates** (Python), **entry discovery** (TypeScript). The floor and the region cap are not binding |
| Achievable ceiling, same pool, 4,096 units | Python 0.659 → 0.792 (perfect order) → 0.886 (perfect order and pick, floor ignored). TypeScript 0.370 → 0.423 → 0.428 |
| Signal or policy? | **Signal.** Routed neighbours are gold less often (2.5–3.5%) than even lexical ranks 11+ (4–8%). Admitting or promoting them by structure alone cannot reach the oracle |
| Parent-precedence order (preregistered) | **REJECT**: recall identical at every budget in both languages; the loss moves from item cap to budget |

Nothing in `src/` changed. `oxide-select-greedy-v4` stays the selector, and the frozen
baselines are untouched.

## Method

`scripts/pool_trace.py` runs the unchanged scorer (`cb.score`) and records every
`packing.pack` call. It asserts that the recorded bundle's recall equals the scorer's, and
both dev runs reproduce baseline-v13 (15/15) and typescript-v10 (26/26) `compat` hashes.
It then re-packs the **same pool** under counterfactuals. Each verified gold line is
credited to its *holder*, the pool entity whose view would carry it (as in
`contextbench.score`'s loss stages).

| Counterfactual | Changes |
| --- | --- |
| `oracle_order` | gold holders above the floor go first; same budget, floor and caps |
| `oracle_pick` | gold holders go first even below the floor |
| `inf_capped` / `inf` | unlimited budget, item cap kept / removed (floor kept) |
| `nofloor_inf` | unlimited budget, no item cap, floor 0 (= routed coverage) |
| `noitemcap` | `MAX_ITEMS` removed at the real budget |
| cap sweep | `MAX_REGIONS` (and `GRAPH_NODES`) at 16/32/48/96/128, production policy |

## Ceilings (dev, macro `line_recall`, verified)

([results/pool-py-dev.txt](results/pool-py-dev.txt), [results/pool-ts-dev.txt](results/pool-ts-dev.txt))

| | Python @1024 | Python @4096 | TypeScript @1024 | TypeScript @4096 |
| --- | ---: | ---: | ---: | ---: |
| production | 0.548 | 0.659 | 0.330 | 0.370 |
| `oracle_order` | 0.682 | 0.792 | 0.370 | 0.423 |
| `oracle_pick` | 0.776 | 0.886 | 0.372 | 0.428 |
| `noitemcap` | 0.548 | 0.659 | 0.330 | 0.370 |
| `inf_capped` | 0.799 | 0.799 | 0.380 | 0.380 |
| `inf` | 0.948 | 0.948 | 0.429 | 0.429 |
| `nofloor_inf` (routed coverage) | 0.957 | 0.957 | 0.443 | 0.443 |

What this decomposes into at 4,096 units:

- **Discovery:** Python 0.043 of gold lines have no holder in the pool; TypeScript 0.557.
  For TypeScript, entry retrieval dominates: at 4,096 units no selection change can exceed
  +0.058 (`oracle_pick`), and +0.073 with an unlimited budget.
- **Floor:** with an unlimited budget it costs +0.009 (Python) and +0.014 (TypeScript).
  Below-floor gold is mostly a method inside a class that packs anyway. At a fixed budget
  it matters only for the oracle (`oracle_pick` − `oracle_order`: Python +0.094 from
  flask-5014, requests-1142 and -2317: small members whose enclosing class is too large).
- **Order / budget allocation:** Python +0.133 at the same cost, from 5 tasks; this is
  the largest single loss after routing.
- **Item cap (`MAX_ITEMS` 24):** it binds on 2 Python dev tasks at 4,096 (requests-2931,
  -5414), but removing it changes no recall: the budget takes the same gold instead. It
  binds before the budget only at unlimited budgets (0.799 vs 0.948).
- **Region cap:** caps from 32 to 128 change no task in either language; 16 loses one
  Python task (−0.015). `GRAPH_NODES` (64) never fires in either frozen baseline, because
  the router returns at most `MAX_REGIONS` candidates. The 64-region cap is not where
  graph evidence is lost. Larger caps only add depth-2 regions, which are valued below the
  floor (router-reachability, graph-v2).

## Relevance signal vs policy (4,096 units)

Gold rate = candidates holding any verified gold line ÷ candidates in the class:

| Class | Python n | gold rate | TypeScript n | gold rate |
| --- | ---: | ---: | ---: | ---: |
| lexical rank 1–5 | 71 | 0.197 | 117 | 0.145 |
| lexical rank 6–10 | 67 | 0.090 | 104 | 0.096 |
| lexical rank 11+ | 117 | 0.077 | 199 | 0.040 |
| routed depth-1 symbol (0.45) | 255 | 0.035 | 484 | 0.025 |
| routed depth-2 symbol (0.3, below floor) | 111 | 0.027 | 134 | 0.000 |
| routed-only file (0.2, below floor) | 84 | 0.012 | 188 | 0.037 |

Units packed: lexical entries take 31,821 (Python) / 67,190 (TypeScript) units summed
over tasks, of which 14% / 11% sit in items touching gold. Routed neighbours take 2,687 /
717 units, with 0% touching gold. The current value order (lexical before routed, routed
depth 2 and files below the floor) matches the measured rates. Graph evidence is not held
back by a restrictive policy: structure alone does not mark the gold neighbour. The one
sub-class with a higher rate, Python depth-1 *call* neighbours of entries 1–3 (3 of 25,
0.12), is 3 candidates.

## Preregistered study: parent-precedence order

[preregistration.md](preregistration.md) was frozen after the Python dev trace and
before the TypeScript count or any arm number. Among the depth-1 symbols (all valued
0.45), `pack` follows pool position, which is `(depth, key)`, i.e. alphabetical. Arm R
orders them by the best position of the entry point they were reached from. It changes
no value, cap or pool membership, so context cost is matched.

- Coverage precondition: 9 dev tasks (4 Python, 5 TypeScript) had a lost gold holder
  behind a worse-parented packed neighbour ([results/order-precondition.txt](results/order-precondition.txt)),
  above the required 5.
- Dev gate ([results/order-gate-dev.txt](results/order-gate-dev.txt)): B reproduces both
  frozen baselines. R changes the payload of 14 of 40 tasks and **no recall at any budget**
  (pooled `line_recall@4096` +0.000, needed +0.010); `gold_unit_share@4096` −0.0002.

**REJECT.** Held-out was not run. The targeted spans move, but onto the budget:
requests-5414 `prepare_url` goes from `item cap` to `omitted, budget`, and requests-2931
from `item cap` to `reduced view`. The other neighbours of entry 1 (e.g.
`HTTPAdapter.send`, 1,605 units) also move up and spend the rest. Order within the
neighbour tier is not the constraint. All lexical entries (≥ 0.5) pack before any
neighbour, and they leave too little for one more 800–1,000-unit method.

## Failed hypotheses

- **"Selection policy is too restrictive for graph evidence."** Not supported. The floor
  is nearly free at unlimited budget, the caps are inert, and routed neighbours are
  rarer gold than any lexical rank class.
- **"The arbitrary (alphabetical) order among routed neighbours loses gold."** Rejected
  (above). The gold neighbour wins the tie but still does not fit.

## For the next milestone

1. **The headroom is real but needs a per-candidate relevance judgment.** At matched cost,
   a perfect order is worth +0.133 (Python) / +0.053 (TypeScript) at 4,096. Structural
   features tested so far (depth, edge kind, PPR, communities, dispatch edges, parent
   precedence) do not separate gold. This is the `DecisionProvider` seam behind
   `packing.value` (BOOTSTRAP § Remaining 2): build it with a replay fake, and score
   candidates against `oracle_order` as the upper bound, before any learned or JEV judge.
2. **TypeScript:** 56% of gold lines are never routed, mostly with no entry point in the
   file. Entry retrieval, not selection or graph, bounds TypeScript.
3. Do not raise `MAX_REGIONS`, `MAX_ITEMS` or lower `INCLUDE_FLOOR` on this evidence.

## Reproduce

Same corpora and caps as graph-v2 (`TMPDIR=~/Projects/oxide-eval-data/oxide-graph-eval/tmp`,
`systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200%`).

```sh
uv run python docs/phase-6/graph-v3/scripts/pool_trace.py py py-dev.json   # and ts ts-dev.json
python docs/phase-6/graph-v3/scripts/pool_summary.py py-dev.json
python docs/phase-6/graph-v3/scripts/order_precondition.py py-dev.json ts-dev.json
uv run python docs/phase-6/graph-v3/scripts/order_arm.py py dev B OUT_B   # and R; ts likewise
python docs/phase-6/graph-v3/scripts/order_gate.py dev py=OUT_B,OUT_R ts=TS_B,TS_R
```
