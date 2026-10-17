# Large repositories: the LadybugDB load pool

Measured 2026-10-17 on this laptop (2 CPUs and 3 GB through `systemd-run`, as for every
heavy run), on the 81 pinned corpora of [TypeScript ContextBench v1](phase-6/typescript-v1/README.md).

## Root cause

Before this change every LadybugDB database had the same fixed 128 MiB buffer pool.
A generation is loaded in one transaction (`COPY` of every table, then the FTS index),
and LadybugDB keeps what the transaction writes in the pool until it commits. VS Code's
store is 291 MB: all 14 pinned VS Code commits failed with `Buffer manager exception:
… The buffer pool is full and no memory could be freed!` in `COPY Rel` or `COPY Opaque`.
Nothing was published, and the previous generation stayed current.

Two alternatives were measured on the largest corpus (`vscode@9a281018`, 89,927
entities, 553,816 relations) and rejected:

- A transaction and a `CHECKPOINT` per table, or per 100,000 or 20,000 rows: still fails
  at 128 MiB (in the first `CHECKPOINT`, or near the end of `Opaque`). The pool a load
  needs grows with the data loaded, not with the size of each step.
- Raising the fixed pool: this works, but it raises the cap for every repository, and
  still fails for a repository bigger than the new constant.

## What a load needs (measured)

The smallest pool that loads, in 32 MiB steps, against the size of the CSV files the
load copies:

| Corpus | Entities | Relations | CSV MiB | Smallest pool (MiB) |
| --- | ---: | ---: | ---: | ---: |
| code-server@78658f1c | 500 | 4,410 | 0.8 | ≤ 64 |
| vue@c0c9432b | 5,205 | 51,922 | 13 | ≤ 64 |
| mui@b00bdc76 | 14,225 | 84,635 | 22 | ≤ 64 |
| vscode@8c76afad | 63,400 | 377,657 | 121 | 192 |
| vscode@2dff5dee | 78,421 | 484,827 | 158 | 256 |
| vscode@9a281018 | 89,927 | 553,816 | 182 | 288 |

OXIDE's own `src/` (290 entities) needs 16 MiB: a fixed overhead plus about 1.6 MiB
per CSV MiB.

## The change

The pool is a ceiling: LadybugDB allocates frames as it uses them. A load now gets
`BUFFER_POOL + 2 × CSV bytes` (`src/oxide/storage/ladybug.py`, `LOAD_POOL_PER_BYTE`).
Read views keep the 128 MiB pool, which serves every query on the largest store.

- **Small repositories:** the pool is about the same, so their memory and behaviour
  don't change.
- **Exhaustion:** the pool is bounded by what is loaded, so a load that needs more
  still fails with LadybugDB's error. Publication stays atomic: a failed load publishes
  nothing.
- **Results:** nothing a query returns depends on the pool, and the TypeScript gate
  checks this (below).
- **Staging space:** the four CSV files are written before the database is opened
  (182 MiB for the largest VS Code load). Before, they were written one at a time.

## Results

All 14 VS Code corpora (368,952–553,816 relations) now index with the default
settings. Each run below is index, search and a 4,096-unit build, in a fresh process:

| | Default-pool failures | Peak RSS (MB) | Store (MB) |
| --- | ---: | ---: | ---: |
| Before (128 MiB fixed) | 14 of 14 | 829 at failure | — |
| Before, 512 MiB override (the harness's retry) | 0 | 1,177 (largest) | 291 |
| After (default) | 0 of 14 | 834–1,177 | 195–291 |

Peak RSS on the largest corpus is the same with a 320 MiB or a 512 MiB override as
after this change. The pool's ceiling therefore costs nothing; the memory is what the
load uses, about 350 MB above the 829 MB that derivation holds before `COPY`.
`tests/python/test_persistence.py` keeps a scaled-down case: at a 16 MiB floor, 40,400
relations overflow a fixed pool and load with the per-byte pool.

## Limits

- The factor 2 rests on six measured loads up to 554k relations: the measured need is
  at most 1.62× the CSV bytes. A repository whose rows weigh more on LadybugDB's side
  (much longer keys, a different schema) would need the factor measured again.
- The peak memory of a large index is mostly derivation: Python holds every entity
  and relation before the load. This change does not reduce it. Streaming the load
  would reduce it, but it is a separate design.
