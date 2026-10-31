# typescript-v15: PR #45 review fixes (TypeScript callback scopes, bounded routing reads)

Versions: `typescript` `oxide-typescript-v4` and facts `oxide-typescript-facts-v3`
(callback and block bodies are opaque scopes in TypeScript/TSX, as they already were in
JavaScript, so a declaration hidden in a callback can no longer capture an outer call),
`python` `oxide-python-v7`, `java` `oxide-java-v2`, and `router` `oxide-router-bfs-v3`
(store reads bounded by an edge allowance). The derivation id changes for every index.

## Measured against typescript-v14

| File | Difference from v14 |
| --- | --- |
| `metrics.json` | byte-identical (every gated metric, both splits) |
| `results.json` | 89 `versions.derivation`, 89 `versions.router`; 4 tasks' routed sets ([12], [16], [20], [60]) and 9 tasks' `route.deferred`/`fanout_truncations`/omission counts; task [60] now stops by `depth`, not `regions`. No `stopped_by: "edges"` |
| `manifest.json` | 82 corpus `derivation` ids, `versions`, `oxide_source`, and resolution counts in 81 of 82 corpora. The `baseline`/`protocol` labels are fixed strings and unchanged, as in v14 |

Resolution totals over the 82 corpora (measured):

| | v14 | v15 | change |
| --- | ---: | ---: | ---: |
| calls resolved | 1,330,762 | 1,191,021 | −139,741 (−10.5%) |
| calls ambiguous | 244,076 | 216,060 | −28,016 |
| calls unresolved | 4,228,486 | 4,396,243 | +167,757 |
| references resolved | 88,753 | 88,665 | −88 |

The lost edges are calls to declarations inside callback or block bodies (for example a
helper declared inside a `describe(() => …)` callback), which are no longer entities'
children and so stay unresolved; calls from callbacks to outer declarations still
resolve (checked on a probe). This is the JavaScript policy applied to TypeScript: it
gives up those local edges so a hidden declaration can never be a false target.
Gated quality is unchanged; the routing differences are inside tolerance.

`performance.json` is re-measured under the 200% CPU / 3 GB cap: `index_s` is
0.74–1.19× v14's (median 0.86×); `max_rss_mb` is 33 MB lower to 53 MB higher. Different
sessions, so a diagnostic only.

Command: `OXIDE_CONTEXTBENCH_TS=1 uv run --locked python tests/python/contextbench_ts.py run --out docs/phase-6/typescript-v15`.
