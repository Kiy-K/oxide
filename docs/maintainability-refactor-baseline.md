# Maintainability-refactor baseline

Fresh observations captured before the behavior-preserving refactor at
`bff909a` (2026-09-08). They supplement, rather than replace,
[`canonical-baseline.md`](canonical-baseline.md) and
[`perf-baseline-v0.1.md`](perf-baseline-v0.1.md).

## Fixture retrieval

```sh
cargo build --release -j 2
./target/release/oxide eval --config fixtures/benchmark.json
```

| mode | recall@5 |
|---|---:|
| vector | 0.8181818 |
| hybrid | 0.90909094 |

## Synthetic offline performance

```sh
scripts/perf.sh 200
```

One hashed-embedder run over 804 files / 3,412 symbols:

| measurement | observation |
|---|---:|
| cold index | 392 ms / 26,180 KB RSS |
| no-change index | 21 ms / 23,360 KB RSS |
| single-edit index | 48 ms / 25,700 KB RSS |
| search | 0.04 s |
| context | 0.04 s |
| index database | 6.3 MB |

These are diagnostic single-run observations, not new CI thresholds. Follow
`perf-baseline-v0.1.md`: investigate only material movement with three fresh
runs and compare medians.

## Post-refactor check

After `cb2b178`, the same offline command produced:

| measurement | before | after |
|---|---:|---:|
| cold index | 392 ms / 26,180 KB RSS | 400 ms / 28,608 KB RSS |
| no-change index | 21 ms / 23,360 KB RSS | 30 ms / 24,044 KB RSS |
| single-edit index | 48 ms / 25,700 KB RSS | 53 ms / 25,936 KB RSS |
| search | 0.04 s | 0.04 s |
| context | 0.04 s | 0.05 s |
| index database | 6.3 MB | 6.3 MB |

Fixture retrieval remains vector Recall@5 `0.818` and hybrid Recall@5 `0.909`.
The one-run latency/RSS changes are inside the documented diagnostic-noise
range; no threshold was adjusted. `scripts/perf.sh` now explicitly clears
HTTP-provider variables and pins `OXIDE_EMBED_NATIVE=hashed`, so this command
cannot accidentally benchmark the shipped native model instead.
