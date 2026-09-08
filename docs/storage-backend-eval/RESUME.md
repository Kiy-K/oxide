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

The freeze wording is already in `AGENTS.md` ("The backend question is
**closed**"), so the plan's substance is delivered. Genuinely outstanding:

- **The plan Ref is not marked complete.** Post the completion update to
  https://plan.ref.tools/gxMECSPTsyIsd0Cd .
- **Gates run at N=450/900 only.** `baseline.md`'s table goes to 1500
  (25,512 symbols). Re-run `scripts/perf.sh 1500` for the new numbers if the
  larger point matters — the cold-index and DB-size regressions both grow
  linearly with symbol count, so that row is the least flattering one and
  should be published rather than skipped.
- **ContextBench Tier A was not re-run.** `docs/canonical-baseline.md` is a
  21-task qwen3 table needing a running llama.cpp server and the cached repos
  under `~/.cache/oxide-contextbench/`. The fixture eval being byte-identical
  is strong evidence ranking did not move, but it is not that table.

## Open risks / things a fresh session should know

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
