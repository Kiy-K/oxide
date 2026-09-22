# PG-1 validation of the rowid-scan challenger

Independent evaluation of
[`../challenger-rowid-scan/`](../challenger-rowid-scan/README.md) under
[PG-1](../prospective-gate.md), the null-calibrated gate committed in
`94bac2c` **before** this run was taken. Issue #10's own gate and the
challenger's failure under it are untouched and still stand; this is a
second, independent verdict under different rules, not a re-scoring of
the first.

**Verdict: PG-1 FAIL — G3 (end-to-end latency) is not met.** G1, G2, G4
and G5 pass; G6 is a judgment, written in §7, and it is also negative.
Production is unchanged, AGENTS.md is unmodified, nothing is merged.

Raw samples: [`raw-null-a/`](raw-null-a/), [`raw-null-b/`](raw-null-b/),
[`raw-base/`](raw-base/), [`raw-chal/`](raw-chal/); the gate script's
full output is [`gate-output.txt`](gate-output.txt).

## 1. Run shape

Four arms through the identical protocol (stages, one-shot CLI, MCP,
index costs), interleaved arm-by-arm within each step, in one window on
the same warm indexed corpora:

| arm | binary | commit |
| --- | --- | --- |
| `null-a` | baseline | `cce3907` |
| `null-b` | baseline (same file) | `cce3907` |
| `chal` | challenger | `65dbbc5` |
| `base` | baseline | `cce3907` |

`null-a` vs `null-b` is the same binary measured twice, so every
difference it shows is this machine's noise at that row's magnitude,
in this window. That is the yardstick the performance gates are stated
against.

## 2. The null table (published before the verdict, as PG-1 requires)

|row|null-a|null-b|null|
|---|---:|---:|---:|
| `pytest` warm `all_symbols` | 20.84 ms | 20.98 | **0.7 %** |
| `pytest` warm `snapshot_load` | 27.34 | 27.40 | 0.2 % |
| `pytest` warm `context` | 40.29 | 40.78 | 1.2 % |
| `pytest` warm `search_noexpand` | 7.62 | 7.30 | 4.1 % |
| `pylint` warm `all_symbols` | 33.89 | 34.02 | **0.4 %** |
| `pylint` warm `snapshot_load` | 41.33 | 41.44 | 0.3 % |
| `pylint` warm `context` | 61.85 | 62.49 | 1.0 % |
| `pylint` warm `all_symbol_relations` | 4.36 | 4.65 | 6.7 % |
| `pytest` CLI `search` | 54.9 | 52.1 | 5.2 % |
| `pytest` CLI `search --no-expand` | 13.8 | 15.1 | 9.4 % |
| `pytest` CLI `query` | 60.6 | 64.9 | 7.0 % |
| `pylint` CLI `search` | 75.0 | 73.4 | 2.2 % |
| `pylint` CLI `search --no-expand` | 20.0 | 19.6 | **1.9 %** |
| `pylint` CLI `query` | 85.5 | 81.8 | 4.4 % |
| `pytest` MCP first `search` / `query` | 58.2 / 58.2 | 61.4 / 57.8 | 5.4 % / 0.8 % |
| `pylint` MCP first `search` / `query` | 79.9 / 76.7 | 74.9 / 78.2 | 6.3 % / 2.0 % |
| `pytest` cold index / reindex / edit | 3,327 / 104 / 352 | 3,335 / 103 / 350 | 0.2 / 0.9 / 0.5 % |
| `pylint` cold index / reindex / edit | 3,963 / 149 / 343 | 3,918 / 148 / 337 | 1.1 / 0.6 / 1.8 % |

Largest null on any gate-relevant row: **16.2 %** (`requests`
`search --no-expand`, a 10 ms row on a small corpus, which decides
nothing). Under PG-1's void rule (> 25 %) the run is **valid**.

Two things this table settles on its own. The in-process stages are
*far* quieter than the ±3–12 % band the original baseline assumed from
batch-to-batch spread — 0.2–1.2 % on the large corpora — so a 19 %
stage improvement is enormous relative to noise. And the one-shot CLI
is 2–9 % noisy at 14–85 ms, which is why §5b's absolute-millisecond
disappointment looked worse than the effect really is.

## 3. G1 — correctness: **PASS**

- Order digests identical between the two binaries on `pytest`,
  `pylint` (568 adjacent `(file, start_line)` ties), `ts_repo` (3 ties)
  and a `VACUUM`ed + `ANALYZE`d copy of `pytest`.
- Parity matrix: 495 outputs, **0 diffs** baseline-vs-challenger and
  **0 diffs** baseline-vs-itself in this same run.
- `oxide eval --config fixtures/benchmark.json` byte-identical
  (hybrid recall@5 0.909, vector-only 0.818).
- `tests/query_plans.rs` passes with and without `ANALYZE`. The
  corpus-load statement — which that file does not pin — changes plan
  from `SCAN symbols USING INDEX idx_symbols_file` + `USE TEMP B-TREE
  FOR LAST TERM OF ORDER BY` to `SCAN symbols`, stated here as PG-1
  requires.
- `mise run verify` green.

## 4. G2 — targeted stage: **PASS**

Requirement: improvement ≥ max(3 × null, 10 %), each large corpus, each
batch.

| corpus | batch | baseline | challenger | improvement | required | |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| pytest | 0 | 21.23 ms | 17.18 | **19.1 %** | 10.0 % (3×null = 2.1 %) | PASS |
| pytest | 1 | 21.62 | 17.12 | **20.8 %** | 10.0 % | PASS |
| pylint | 0 | 34.49 | 25.35 | **26.5 %** | 10.0 % (3×null = 1.2 %) | PASS |
| pylint | 1 | 34.88 | 24.79 | **28.9 %** | 10.0 % | PASS |

The effect is 27–72× the null. Four independent comparison runs now
agree on it (−19 to −30 %).

## 5. G3 — end-to-end latency: **FAIL**

Requirement: at least one uninstrumented surface improves by more than
2 × null on **both** large corpora (met), **and no** end-to-end surface
regresses by more than 1 × null on either (**not met**).

| corpus | surface | baseline | challenger | Δ | 2×null | 1×null | |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| pytest | CLI `search` | 55.3 ms | 47.3 | **+14.4 %** | 10.4 % | — | improves |
| pylint | CLI `search` | 77.4 | 65.1 | **+15.9 %** | 4.4 % | — | improves |
| pytest | MCP first `query` | 56.7 | 50.3 | **+11.4 %** | 1.5 % | — | improves |
| pylint | MCP first `query` | 77.8 | 68.2 | **+12.3 %** | 4.0 % | — | improves |
| pylint | CLI `query` | 87.5 | 76.2 | +12.9 % | 8.8 % | — | improves |
| pytest | CLI `query` | 60.5 | 59.3 | +2.0 % | 13.9 % | 7.0 % | flat |
| pytest | MCP first `search` | 56.0 | 53.1 | +5.2 % | 10.9 % | 5.4 % | flat |
| pylint | MCP first `search` | 75.5 | 67.0 | +11.2 % | 12.6 % | 6.3 % | flat |
| pytest | CLI `search --no-expand` | 14.2 | 13.8 | +2.7 % | 18.8 % | 9.4 % | flat |
| **pylint** | **CLI `search --no-expand`** | **19.4** | **20.6** | **−6.5 %** | 3.7 % | **1.9 %** | **REGRESSES** |

Two surfaces (`search`, MCP first `query`) clear 2 × null on both
corpora, so the improvement half of G3 is satisfied. The gate fails on
the second half: `search --no-expand` on `pylint` is 6.5 % slower
against a 1.9 % null — 3.4× the noise.

### Why this matters rather than being dismissed

`search --no-expand` never calls `all_symbols`; it shares no changed
line with the challenger. The tempting reading is "noise". The data
says otherwise — the regression is reproducible across the whole
series:

| run | `pytest` | `pylint` |
| --- | ---: | ---: |
| run 1 | +9.7 % slower | +3.4 % slower |
| run 3 (issue #10's deciding run) | +10.2 % slower | +7.7 % slower |
| run 4 (this one) | −2.7 % (faster) | **+6.5 % slower** |

Five of six measurements have the challenger slower on the surface it
should not touch, by 3–10 %. The most likely mechanism is binary
layout: `all_symbols` grows from one SQL string plus a 12-line loop to
~90 lines with a second `Vec`, a hash map and a permutation pass, and
`--no-expand` is a 14–20 ms process dominated by startup, dynamic
linking and page-in — exactly the regime where code size shifts show
up. That is not a bug in the change, but it is a real cost on OXIDE's
cheapest surface, and PG-1 was written (before this run) to count
exactly that kind of cost. It counts.

## 6. G4 and G5 — memory, indexing, storage: **PASS**

| gate | measurement | limit | |
| --- | --- | --- | --- |
| G4 | peak RSS on `query`: `pytest` +0.04 MB, `pylint` +0.10 MB | max(0.3 MB, null = 0.03 MB) | PASS |
| G4 | allocations on `all_symbols`: −80 (`pytest`), −1,233 (`pylint`); allocated bytes +0.20 / +0.41 MB, explained in the challenger write-up §1 (the `Vec<i64>` keys, `u32` permutation, `Vec<bool>`) | — | PASS |
| G5 | cold index −0.1 % / +0.6 %; no-change reindex −4.7 % / −9.3 %; single-file edit −1.0 % / −3.4 % | ≤ 1 × null (0.2–1.8 %) | PASS |
| G5 | `index.db` −40 KB (`pytest`) / +22 KB (`pylint`); WAL peak +3.0 % / −7.6 % | one page allocation / checkpoint timing | PASS |

## 7. G6 — maintenance complexity: **FAIL (judgment)**

PG-1 requires the reviewer to name the benefit and the cost in one
sentence, and to say whether the trade is worth it. The five points:

1. **Lines and surface.** +94 / −22 lines in `src/storage.rs`, one new
   free function (`apply_permutation`). No public signature, trait
   contract or invariant *text* changes — but AGENTS.md's
   load-bearing invariant would have to be rewritten, which is the
   whole of §7 in the challenger write-up.
2. **Where the invariant lives.** The change moves the corpus-order
   guarantee out of SQLite — one declarative `ORDER BY` the engine
   enforces — into a Rust comparator whose correctness depends on two
   facts that are true but non-obvious: that `id INTEGER PRIMARY KEY`
   is the rowid, and that the bit-cast making rowid order meaningful is
   *signed*. An earlier draft got the second one wrong and produced a
   silently different corpus order on two of five corpora. That is the
   cost, stated plainly.
3. **Mechanical regression check.** Exists — `retrieval_profile
   --stage order_digest`, committed in `b26d2b1`, and it is what caught
   the `u64`/`i64` mistake. This sub-point passes.
4. **Dependencies.** None added (`rustc-hash` was already a direct
   dependency). Passes.
5. **Counterfactual.** Reverting is a clean one-hunk revert, and the
   order digest proves the revert is behaviour-preserving. Passes.

**The judgment:** a 19–29 % cut in an in-process stage and a 12–16 %
cut on one-shot `search` and the MCP first call is a real benefit, and
it does not justify moving a correctness invariant from SQLite into a
90-line Rust routine whose first draft already broke that invariant
once — not while the surfaces users touch most often (`query` on
`pytest`, both `--no-expand` surfaces, every steady-state MCP call) see
nothing, or see a reproducible 3–10 % regression. G6 fails.

## 8. Verdict and what would change it

**PG-1: FAIL** (G3 and G6; G1, G2, G4, G5 pass). Combined with the
issue #10 gate's failure, the rowid-scan challenger is **rejected
twice, under two independently-written gates, for two different
reasons** — absolute magnitude then, end-to-end cost and maintenance
now. It stays on `challenger/rowid-scan` as recorded, reproducible
evidence. SQLite stays authoritative; `main`'s `all_symbols` is
untouched; AGENTS.md's SQL-ordering invariant is unmodified.

What would make it worth revisiting, in order:

1. **The `--no-expand` regression explained and removed.** If it is
   binary layout, the same ordering change written in ~20 lines (for
   example: keep the SQL `ORDER BY` and attack only the per-row rowid
   seek, or sort in place without the second key array) might not
   trigger it. That is a different patch, judged on its own.
2. **A corpus where the load actually dominates.** At 7–14k symbols the
   corpus load is 40–60 % of one *expanding* request and 0 % of
   everything else. At 50k+ (the scale AGENTS.md already names as the
   brute-force vector scan's ceiling) the balance changes, and both
   gates would read differently.
3. **A schema-level fix instead.** The baseline's §9 alternatives — a
   compact `references` encoding, or a side table so rows fit their
   page — target the other half of the load (decode + allocation,
   1.7–2.5 µs/symbol) and do not move any ordering guarantee. They need
   the architecture discussion first, per AGENTS.md, and PG-1 does not
   cover them.
