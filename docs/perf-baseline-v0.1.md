# v0.1 performance baseline

Reproducible with `scripts/perf.sh <modules_per_lang>` against a deterministic
synthetic repo (`scripts/gen_bench_repo.py`) with the offline hashed embedder
(no network/model dependency — keeps numbers reproducible across machines
without a running llama.cpp server). Real-repo indexing times (flask,
pytest, django, darkreader, code-server) were spot-checked separately for
correctness in Phase 1.2 section A/B; this file is the synthetic scaling
baseline used for regression thresholds.

## Environment

- CPU: Intel Core i7-13620H (16 logical cores), one run, no other significant
  load; not an isolated benchmarking rig — treat as relative indicators.
- OS: openSUSE Tumbleweed, Linux 7.1.8
- Rust: 1.98.0, `cargo build --release` (`lto = "thin"`, `codegen-units = 1`)
- Embedder: offline `HashedEmbedder` (no external process)
- Each row is a single run, not an average of repeats — see "Noise" below.

## Results (2026-08-29)

| repo size (files/symbols) | cold index | no-change reindex | single-edit reindex | search (best/3) | context (best/3) | peak RSS (cold) | index size |
|---------------------------|-----------:|-------------------:|---------------------:|-----------------:|-------------------:|-----------------:|-----------:|
| 804 / 3,211                | 378 ms     | 40 ms               | 41 ms                 | 0.06 s            | 0.07 s              | 20.5 MB           | 5.8 MB     |
| 2,404 / 9,611               | 1,069 ms   | 126 ms              | 153 ms                | 0.17 s            | 0.23 s              | 39.1 MB           | 18 MB      |
| 6,004 / 24,011              | 2,614 ms   | 385 ms              | 314 ms                | 0.43 s            | 0.57 s              | 80.1 MB           | 43 MB      |

A single-symbol edit rewrites (2 changed symbols — the touched function plus
its enclosing module whose body hash includes the first line) and reuses
every other embedding (e.g. 24,009/24,011 reused at the largest size).
Cold-index time scales roughly linearly with repo size (not worse); peak RSS
scales sub-linearly relative to index size, both healthy — no observed
quadratic cliff up to 6k files / 24k symbols.

## Noise

These are single-run numbers, not statistical baselines — do not treat a
±20% delta between two individual runs as a regression. `search`/`context`
in particular are sub-100ms-to-low-hundreds-of-ms at this scale, where OS
scheduling jitter and cold page cache dominate the signal. Before declaring
a regression, re-run 3x and compare medians.

## Regression thresholds (catastrophic-only, not incidental-noise-sensitive)

Intentionally generous — the goal is catching a real algorithmic regression
(e.g. an accidental O(n²) loop, a full-table scan added to a per-symbol
path), not pinning exact timings in CI:

- Cold index time: fail if more than **5x** this table's value at the
  matching (or nearest) repo size.
- No-change reindex time: fail if more than **5x** — this is the case most
  sensitive to an accidental "always re-embed" regression.
- Peak RSS: fail if more than **4x** this table's value at matching size.
- Index size on disk: fail if more than **3x** — a runaway growth here
  usually means a dedup/cleanup path broke (stale rows not purged).

No CI test currently enforces these — `scripts/perf.sh` is a manual/local
harness. Wiring a CI gate on shared/variable-load runners would produce
false positives from noise rather than real signal; a human re-running
`scripts/perf.sh` and comparing against this file is the current process
until CI hardware is dedicated enough to trust automatically.

## Reproduce

```bash
cargo build --release
scripts/perf.sh 200    # ~804 files, matches row 1
scripts/perf.sh 600    # ~2,404 files, matches row 2
scripts/perf.sh 1500   # ~6,004 files, matches row 3
```
