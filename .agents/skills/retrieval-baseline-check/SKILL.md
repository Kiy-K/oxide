---
name: retrieval-baseline-check
description: Checks OXIDE v2 retrieval, routing and packing against the frozen Phase 3 baseline before and after a change to src/oxide/core/ (terms, retrieval, routing, packing) or the lexical/FTS path in src/oxide/storage/. Use before and after touching ranking, scoring, seeds, TreeRouter bounds or selection/packing.
---

`docs/phase-3/baseline-v1/` is a read-only oracle (SPEC § Evaluation,
`docs/agents/workflow.md` § Frozen baselines). The Python engine must reproduce
it exactly: entry points, lexical ranks, BM25 scores to 1e-12 relative, and
routed entities and depths.

## Check (every retrieval-, routing- or packing-adjacent change)

```bash
uv run pytest tests/python/test_baseline.py -q      # exact Phase 3 parity + Phase 4 recall floor
uv run pytest tests/python/test_retrieval.py tests/python/test_context.py -q
```

Run both before the change too. A difference the change wasn't meant to cause is
a regression: find the cause before going further. `test_baseline.py` also
checks the Phase 4 context-quality floor (recall 1.0 at 1,024 units), and every
bundle must keep `used <= budget`.

If a change is meant to move retrieval, the old baseline is not edited. Record
a new baseline directory with a stated comparison against the old one.
Regenerating or loosening a frozen artifact to make a test pass is never
allowed.

## Latency (only when the change targets performance)

```bash
uv run python scripts/bench.py fixtures/py_repo --iterations 5
```

Compare against a run on unchanged code under the same limits (`systemd-run
--user --scope -p CPUQuota=200% -p MemoryMax=3G`). Report the medians and the
corpus `snapshot_id` from the JSON report. Keep negative results in the eval docs.

Legacy v1 harnesses (`cargo`, `fixtures/benchmark.json`, ContextBench under
`eval-agent/`) belong to the retired implementation on `main` / `ac985b28` and
are not v2 gates.
