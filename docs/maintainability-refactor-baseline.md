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
