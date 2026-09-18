---
name: verify
description: Runs OXIDE's full pre-commit checklist (fmt, clippy, test, benchmark gate) in the order that matters and reports pass/fail. Use before committing any change to this repo, or when asked to "verify", "check everything passes", or "run the checklist".
---

Run these in order — order matters, per `AGENTS.md`:

```bash
cargo fmt --check
cargo clippy -j 2 --all-targets -- -D warnings
cargo test -j 2
```

`cargo test` already includes `tests/benchmark_gate.rs`, which fails unless
hybrid retrieval recall@5 is still ≥ vector-only recall@5 on
`fixtures/benchmark.json` — no separate benchmark step is needed for a normal
change.

Stop at the first failing step and report it — do not run later steps against
code you know is broken. If `cargo fmt --check` fails, run `cargo fmt` and
re-check rather than hand-editing whitespace.

If the change touched `src/retrieval.rs`, `src/config.rs`, or embedding
selection logic, also do the extra retrieval-diff check described in the
`retrieval-baseline-check` skill before declaring the change verified — the
benchmark gate only catches a hybrid-vs-vector-only regression, not a
same-direction shift in the actual numbers.

Report each step's pass/fail plainly. Don't claim success for a step you
didn't actually run.
