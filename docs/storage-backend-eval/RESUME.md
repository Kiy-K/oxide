# Resume note — Enhanced SQLite round

Session paused 2026-09-08. Everything below survives a reboot; nothing is
left in a temp directory that matters.

Plan (approved): https://plan.ref.tools/gxMECSPTsyIsd0Cd

## State: committed and green

`0d5a903 feat: persist BM25 postings in SQLite, declare foreign-key enforcement`
on `main`. `cargo fmt` / `clippy --all-targets` / `cargo test` all clean,
benchmark gate passes, and `oxide eval --config fixtures/benchmark.json` is
byte-identical to the pre-change output.

Tasks 1–5 of the plan are **done** and written up in
[`enhanced-sqlite.md`](enhanced-sqlite.md):

1. Foreign-key audit — the premise inverted: enforcement was ON by accident of
   `libsqlite3-sys`'s `-DSQLITE_DEFAULT_FOREIGN_KEYS=1`. Now declared
   explicitly; two dead anti-join sweeps deleted.
2. Persisted BM25 — shipped, bit-exact parity, generation/completeness key.
3. FTS5 trigram — measured, works, rejected (no consumer, +34 MB).
4. Structural graph — SQL push-down rejected on the access pattern; recursive
   CTEs rejected (expansion is one hop).
5. Native tuning — `foreign_keys` kept; `optimize`/`ANALYZE`/`cache_size`
   rejected with numbers.

Codex reviewed the change and found three defects; all three are fixed in the
commit and recorded in the write-up.

## What is left (Task 6 remainder)

All three follow-ups are now closed:

- **`scripts/perf.sh 1500` — done, both sides measured.** Pre-change binary
  built from `ca5ff68` and run on the same machine: cold index 8.0 s -> 22.2 s
  (~2.8×, the worst ratio in the round), `search` 0.27 -> 0.14 s, `context`
  0.34 -> 0.20 s, db 47 -> 70 MB. Three further after-runs were discarded as
  contended; the reasoning is in [`enhanced-sqlite.md`](enhanced-sqlite.md).
- **Real-repo parity — done, and it replaced the Tier A gap with better
  evidence.** `examples/lexical_parity_real_repos.rs` compared the persisted
  and in-memory scorers over 137,731 scored documents on four ContextBench
  repositories, upgrading pre-feature indexes in the process: bit-exact
  everywhere. Run it CPU-capped and with explicit repo arguments.
- **ContextBench Tier A — closed as not applicable.** `canonical-baseline.md`
  is a qwen3 table; re-running against it needs a model ruled out on
  operational cost, and re-running under Arctic would be a new baseline rather
  than a comparison. Reasoning in the write-up's Freeze section.
- **The plan Ref is updated** with the completion note.

## Open risks / things a fresh session should know

- **Cold index grows worse with corpus size than first thought.** Same-machine
  ratios are 2.2× (N=450), 2.25× (N=900) and **2.8× (N=1500)** — slightly
  super-linear, because the posting-row count is. Extrapolating from the
  smaller points understates it; measure, do not infer.
- **Cold index roughly doubled** (5.2 s → 11.6 s at 15,312 symbols) and
  **`index.db` grew 50%** (28 MB → 42 MB). This is the real cost of the
  round, it is intrinsic to writing ~700k posting rows, and three attempts to
  reduce it all failed (see the write-up's table). If that trade is judged
  wrong, the revert is clean: `LexicalIndex::build` is still the fallback path
  and still exercised by tests, so backing out means dropping the generation
  key's publication, not unpicking the scorer.
- The lexical backfill writes outside `replace_file`'s transaction, which is
  safe *only* because those files' symbols are not being rewritten and because
  a racing writer is detected by an in-transaction `content_hash` re-check
  that withholds the generation key. Do not relax either half.
- `docs/storage-backend-eval/spike/Cargo.lock`, `.agents/`, `.codex/` were
  untracked before this session and were deliberately left untracked.

## Reproducing anything in the write-up

```bash
cargo build --release -j 2
export TMPDIR=/home/khoi/.cache/oxide-perf     # ext4, not tmpfs
for n in 450 900; do scripts/perf.sh $n; done

W=$TMPDIR/probe-repo                            # already built; regenerate with:
python3 scripts/gen_bench_repo.py $W 900
(cd $W && OXIDE_EMBED_NATIVE=hashed /home/khoi/Projects/oxide/target/release/oxide index .)

cargo run --release --example lexical_build_probe -- $W
cargo run --release --example sqlite_enhancements_probe -- $W   # copies the db; safe
./target/release/oxide eval --config fixtures/benchmark.json
```
