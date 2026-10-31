# TypeScript ContextBench v2 (`contextbench-ts-v2`)

v2 amends the [v1 protocol](../typescript-v1/README.md) for two changes. They are
measured separately so each can be attributed:

- **Reliability:** the LadybugDB load pool now scales with what it loads
  ([docs/large-repositories.md](../../large-repositories.md)), at commit `dd26e87`.
- **Resolution:** bare specifiers resolve through `tsconfig.json` `paths` and `baseUrl`
  (`oxide-typescript-v2`, [docs/typescript.md](../../typescript.md#relations-and-resolution)).

v1 stays frozen. It is reproducible at `266f3ec` (or `dd26e87`) with its sources-only
corpora (`~/.cache/oxide/contextbench-ts/corpora/`).

## Amendment: the corpora carry their project files

A v2 corpus is a v1 corpus plus every tracked `tsconfig*.json` at the same commit
(file names `tsconfig.json`, `tsconfig.*.json`; 1 to 59 per corpus). It lives in
`~/.cache/oxide/contextbench-ts/corpora-v2/` and is fetched by `contextbench_ts.py fetch`.
Its identity (`tree_sha256`) hashes the TypeScript and config files; `configs` counts the
latter. A resolver that reads configuration needs them, and every real checkout has them.

These are v1's, unchanged:
- tasks, gold audit, splits and scorer (`contextbench-ts-scorer-v1`);
- budgets, tolerance, gated metrics and the cost rule.

`tests/python/contextbench_ts.py` reads `tasks.json` and `gold-audit.json` from
`typescript-v1/`.

The config files are file evidence for OXIDE (coverage `unsupported`). Adding them
changes BM25's document statistics, so the old engine alone, on v2 corpora, reorders
the entries of 18 tasks (15 held out, 3 dev). For example, code-server-4923 has the
same 20 entries in another order. Held-out `span_recall@4096` moves from 0.1938 to
0.2027 for that reason, with no engine change. **That is why every before/after below
uses the same v2 corpora**: `before/` is commit `dd26e87`, without resolution, on them.

## Reliability

At `dd26e87`, the v1 harness on the v1 corpora gives a `results.json` and a
`metrics.json` byte-identical to the frozen v1. No corpus records a
`default_index_failure`; in v1, all 14 VS Code corpora did. Every corpus in
`before/` and here indexes with the product's settings. Memory and timing are in
[large-repositories.md](../../large-repositories.md#results).

## Resolution: correctness and reachability

**No change without configuration.** The v2 engine on the v1 corpora reproduces v1's
`results.json` exactly, apart from `versions.derivation`, and its `metrics.json` is
byte-identical. The rules only fire when a `tsconfig.json` sets `paths` or `baseUrl`.

**Name relations, summed over each repository's corpora** (`manifest.json` `resolution`):

| Repository | Imports resolved | Imports ambiguous | Imports unresolved | Calls resolved |
| --- | ---: | ---: | ---: | ---: |
| vscode (14) | 39,882 → 636,966 | 452 → 152,586 | 802,296 → 53,078 | 405,709 → 916,306 |
| mui (48) | 136,711 → 256,793 | 140 → 846 | 439,921 → 319,133 | 101,811 → 235,098 |
| vue (15) | 43,856 → 70,962 | 1,253 → 3,926 | 35,039 → 5,260 | 122,752 → 173,764 |
| code-server (2) | 686 → 688 | 0 → 0 | 437 → 435 | 1,279 → 1,283 |
| darkreader (2), fixture | unchanged | | | |

- **Correctness**, measured on dev (vscode@1a599e2d): 57,190 bare imports now
  resolve. The harness's path oracle, written independently of OXIDE, names the same
  file for 56,978. The other 212 are named imports that the target module re-exports
  (`export { Orientation } from './gridview'` in `grid.ts`), resolved to the declaring
  file, which the oracle cannot follow. None contradicts the oracle.
- **Ambiguity:** ambiguous imports grow because more of them now reach TypeScript's
  declaration merging, such as interface and `const` service identifiers
  (`IInstantiationService`) or enum and namespace `Severity`. Such a name denotes
  several declarations, and the v1 rules keep it ambiguous rather than picking one.
- **What stays unresolved, sampled on dev:** AMD plugin specifiers
  (`vs/css!./media`), and names their module rebinds or re-exports with
  `export import`.
- **Unresolved by design, from configuration (not sampled):** mui's root `paths`
  doesn't map some of its workspace packages, such as `@mui/utils` and
  `@mui/icons-material`. TypeScript reaches those through `node_modules`, which OXIDE
  doesn't read. In mui@03b616a1, 4,807 imports the oracle can follow to a workspace
  package remain (7,818 before).
- **Project ownership:** 18 of 3,902 TypeScript files in vscode@1a599e2d have no
  owning project (no `tsconfig.json` above them includes them), so their bare
  specifiers stay unresolved.

**Where verified spans are lost at 4,096 units, and why** (all 384 verified spans;
before → after, same corpora):

| Stage | Before | After |
| --- | ---: | ---: |
| entry | 268 | 265 |
| routing | 26 | 30 |
| selection | 23 | 21 |
| budget | 25 | 26 |
| complete | 42 | 42 |

| Cause of the spans lost before packing | Before | After |
| --- | ---: | ---: |
| unresolved import | 71 | 22 |
| router bound | 64 | 120 |
| no path | 159 | 153 |
| partial file | 0 | 0 |

Resolution makes 49 of the 71 spans that sat behind an unresolved import reachable
within the attribution's distance, and 6 more spans gain a path. They become
router-bound instead: the router's limits (64 regions, its fan-out) stop before them.
No more spans are packed.

## Quality: no measured improvement

Verified macro means, before → after (same corpora):

| Metric | dev | held-out | all |
| --- | ---: | ---: | ---: |
| `entry_recall@20` | 0.383 → 0.383 | 0.334 → 0.334 | 0.349 → 0.349 |
| `routed_coverage` | 0.446 → 0.468 | 0.462 → 0.457 | 0.457 → 0.460 |
| `span_recall@1024` | 0.320 → 0.320 | 0.123 → 0.123 | 0.184 → 0.184 |
| `span_recall@4096` | 0.358 → 0.358 | 0.203 → 0.203 | 0.251 → 0.251 |
| `line_recall@4096` | 0.370 → 0.370 | 0.239 → 0.239 | 0.279 → 0.280 |

`routed_coverage` by repository:

| Repository | Before | After |
| --- | ---: | ---: |
| vscode | 0.085 | 0.128 |
| vue | 0.450 | 0.527 |
| mui | 0.466 | 0.435 |

Entry and packing metrics are unchanged in every repository. mui's drop is the router
bound at work: new edges compete with the old ones for the same 64 regions. Every gated
metric is within the 0.02 tolerance of `before/` on both splits (largest: dev
`routed_coverage` +0.022, held-out −0.006).

**This milestone measures no context-quality gain.** Entry retrieval is unchanged by
construction, and the reachability it adds is spent by the router's bounds.

## Performance (one run each, not gated)

| Repository | Index median, s | Task median, s |
| --- | ---: | ---: |
| vscode | 23.6 → 24.7 | 1.64 → 1.82 |
| mui | 3.46 → 3.62 | 0.35 → 0.37 |
| vue | 2.37 → 2.75 | 0.41 → 0.41 |

The whole run's peak RSS is 1,272 → 1,229 MB. Each task is a search, a route, and
two builds at each of three budgets. vscode tasks are slower because they have more
edges to route.

## Reproducibility

- Two runs of the v2 engine on the v2 corpora gave byte-identical `results.json`,
  `metrics.json` and `manifest.json`.
- `performance.json` holds wall times and is never compared.
- `mise run contextbench-ts` gates this baseline (the v1 gate's tests, with v2's
  frozen files).

## Implications for the next milestones (evidence above)

1. **Router bounds** now hold the largest share of the spans OXIDE can structurally
   reach (120, up from 64). Raising or reallocating them is a routing change, the
   next lever for TypeScript reachability. It needs its own evaluation against this
   baseline.
2. **Entry retrieval** is unchanged: 265 of 384 spans are lost at entry. That remains
   the separate retrieval research milestone.
3. **Workspace packages reached through `node_modules`** (mui's unmapped `@mui/*`)
   need `package.json` `exports`/`types` semantics. Their targets are mostly build
   output, so they're documented as unsupported rather than guessed.
