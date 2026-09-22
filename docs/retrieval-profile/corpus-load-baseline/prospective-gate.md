# PG-1: a prospective Pareto gate for corpus-load challengers

**Written before the run that it judges.** This file is committed ahead
of any measurement taken under it, so that no threshold here can have
been chosen after seeing a result. It replaces nothing: issue #10's
gate stays exactly as it was written, and the rowid-scan challenger's
verdict under it — [failed on the absolute
thresholds](challenger-rowid-scan/README.md) — stands as recorded.

## Why a new gate was needed

Issue #10's gate (a) asked for an absolute improvement: −5 ms on
`pytest`, −10 ms on `pylint`, warm `all_symbols`, both batches. Those
numbers were read off a baseline measured at one machine state. Three
later runs of the same comparison measured the *same* relative effect
(−19 to −30 %) and three different millisecond values (−4.0 to −10.5),
because the baseline binary itself ran 1.6× faster in the later windows
(`challenger-rowid-scan/README.md` §5b). An absolute threshold on an
unpinned laptop measures the laptop.

PG-1 therefore states every performance threshold **relative to a
same-binary null measured in the same window**, and keeps every
correctness threshold absolute.

## The null control (what makes the rest meaningful)

Every PG-1 run begins by measuring the **baseline binary against
itself**: two arms, `null-a` and `null-b`, both the baseline binary,
run through the identical protocol interleaved with each other. For
each reported row (corpus × stage, corpus × surface, corpus × index
operation) the null delta is

    null(row) = |median(null-b) − median(null-a)| / median(null-a)

This is what two runs of *the same code* disagree by on this machine, in
this window, at this row's magnitude. It is the only honest yardstick
for a delta measured a few minutes later. `null` is computed per row,
not pooled: a 0.9 ms stage and a 60 ms stage have very different noise.

A run whose null exceeds **25 %** on any row that a gate below depends
on is void — the machine was too unstable to decide anything, and the
run is repeated rather than interpreted.

## The gates

A challenger passes PG-1 only if **all six** pass. Any single failure
is a rejection, recorded with its evidence; partial credit does not
exist.

### G1 — Correctness (absolute; no tolerance, no noise allowance)

- `retrieval_profile --stage order_digest` identical between baseline
  and challenger on every corpus in the fixture set, including at least
  one corpus with `(file, start_line)` ties, plus one `VACUUM`ed and one
  `ANALYZE`d database.
- The 495-output parity matrix (440 retrieval + 55 literal) identical,
  and identical baseline-vs-itself in the same run.
- `oxide eval --config fixtures/benchmark.json` byte-identical.
- `tests/query_plans.rs` passes with and without `ANALYZE`; no
  candidate-only statement changes plan. A change to a statement the
  file does not pin must be stated explicitly in the write-up.
- `mise run verify` green (fmt, clippy ×2, tests ×2, benchmark gate,
  installer checks).

### G2 — Repeatable relative improvement on the targeted stage

For each large corpus (≥ 5,000 symbols) and each batch:

    improvement(row) ≥ 3 × null(row)   and   improvement(row) ≥ 10 %

Both conditions, every batch, every large corpus. The 3× multiple is
the run-specific part; the 10 % floor stops a very quiet window from
waving through an effect too small to matter to a user. Small corpora
(< 1,000 symbols) are reported but never decide G2 — their rows are
1–5 ms and their nulls are routinely 10–20 %.

### G3 — End-to-end latency, user-visible

At least one **uninstrumented** end-to-end surface — one-shot
`oxide search` / `oxide query`, or the MCP first call — must improve by
more than `2 × null(row)` on **both** large corpora, and **no**
end-to-end surface may regress by more than `1 × null(row)` on either.
An improvement that exists only in the in-process profiler is not a
user-visible improvement.

### G4 — Memory

Peak RSS (`ru_maxrss`, one-shot CLI) on the heaviest surface must not
rise by more than `max(0.3 MB, null(row))`. Cumulative allocation count
and bytes are reported alongside, and a rise in *either* that is not
explained in the write-up is a failure even when RSS holds.

### G5 — Indexing and storage cost

Cold index, no-change reindex, single-file edit, `index.db` size and
peak WAL: none may regress by more than `1 × null(row)`. `index.db`
must not grow by more than one page allocation attributable to
checkpoint timing; any schema or size change moves the proposal out of
PG-1 entirely and into the architecture discussion AGENTS.md requires.

### G6 — Maintenance complexity, judged and recorded

Not a measurement — a written judgment the reviewer must make
explicitly, in these terms:

1. **Lines and surface**: how many lines replace how many, and whether
   any public signature, trait contract or invariant text changes.
2. **Where the invariant now lives**: if the change moves a guarantee
   from SQLite (declarative, enforced by the engine) into Rust
   (procedural, enforced by us), that is a real cost and must be named.
3. **Mechanical regression check**: any guarantee moved into Rust must
   come with a committed, runnable check that fails when it breaks. A
   guarantee whose only protection is a code comment fails G6.
4. **Dependencies**: a new crate, feature, or build requirement fails
   G6 unless separately justified.
5. **The counterfactual**: state what the code would look like if the
   change were reverted later, and whether reverting is a one-line
   operation or a re-derivation.

G6 passes only if the reviewer writes, in the evaluation document, that
the measured benefit under G2/G3 is worth the complexity under 1–5 —
naming the benefit and the cost in the same sentence.

## What stays outside PG-1

SQLite remains the sole authoritative store. PG-1 judges *how* code
reads that store; it cannot approve a different store, a schema change,
a new index, a migration, a re-index requirement, or a schema-version
bump. Those need the architecture discussion AGENTS.md requires, before
any measurement is worth taking.

## Procedure

```bash
# 1. build both binaries from their exact commits, cargo clean -p oxide between
# 2. null control and comparison, all four arms interleaved in one window
#    on the same warm corpora (null-a and null-b are the same binary):
for step in stages cli mcp index-costs; do
  for arm in null-a null-b chal base; do
    scripts/corpus_load_baseline.py $step --work $W --out $W/raw-$arm \
      --oxide $W/$arm/oxide --profile $W/$arm/retrieval_profile --skip-native
  done
done
# 3. parity, order digests, eval, mise verify
# 4. write the evaluation: null table first, then each gate with its
#    row-level null beside the measured delta
```

The null table is published before the verdict in any document written
under PG-1, so a reader can check the yardstick before the claim.

## Acceptance is still a human step

PG-1 passing is a *precondition* for a production change, not a
decision. AGENTS.md's invariants are edited only after a maintainer
says so explicitly, in words, having read the evaluation.
