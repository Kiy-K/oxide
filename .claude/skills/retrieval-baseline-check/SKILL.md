---
name: retrieval-baseline-check
description: Captures or compares retrieval numbers before/after a change to src/retrieval.rs, src/config.rs, or embedding selection, and calls out any difference as a regression to explain. Use before and after touching ranking, scoring, RRF weights, or the embedding provider.
---

Any change to `src/retrieval.rs` (BM25 + cosine + RRF fusion + structural
expansion) or `src/config.rs` (RRF weights, budget defaults) can silently
shift ranking. This repo treats an unexplained shift as a bug, not a
side effect — see `CLAUDE.md`.

## Fast check (every retrieval-adjacent change)

Run this **before** making the change, save the output, make the change, then
run it again:

```bash
cargo build --release -j 2
./target/release/oxide eval --config fixtures/benchmark.json
```

Compare `mean recall@5` and `mean precision@5` for both `vector-only` and
`hybrid` modes against the pre-change run. `tests/benchmark_gate.rs` (run by
`cargo test`) only asserts hybrid ≥ vector-only — it will NOT catch a change
that moves both numbers down together, or that only changes tie-break
ordering. Run the fixture eval **twice** on unchanged code first if you
suspect nondeterminism; a real fix should make repeated runs identical, not
just close.

If numbers differ and the change wasn't meant to touch ranking: that's a
regression — find the cause before proceeding. If the change intentionally
retunes ranking, update `README.md`'s "Committed regression fixture" table
in the same commit as the code change, so the documented numbers stay true.

## Heavier check (only for a deliberate ranking/weight change)

`docs/canonical-baseline.md` records a ContextBench tier-A run (21 tasks, 7
repos, real `qwen3-Q8_0` embedder via `scripts/embedder.sh start`). This is
slower and requires the embedder server plus cloned task repos
(`eval-agent/third_party/ContextBench`, gitignored) — don't run it for a
routine change. Use `scripts/agent_eval/contextbench_run.py` and
`summarize_cb.py` per `AGENTS.md`, and update `docs/canonical-baseline.md`
with the new numbers and date if the retune is intentional and kept.
