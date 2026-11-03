# baseline-v20: whitespace-adjacent views merged

Payload `oxide-payload-v4`: views of one file separated only by whitespace render as one
item under one heading (v3 merged only overlapping views), so the saved heading units
buy more source. Selection is unchanged.

Against baseline-v19 (same tasks, gold audit, scorer, budgets, corpora, tolerance), verified:
dev span_recall@256 0.047→0.080, line_recall@256 0.180→0.193, other recall unchanged;
heldout span_recall@4096 0.178→0.169, line@4096 0.235→0.233, file_recall@4096 0.621→0.641,
other recall unchanged. gold_unit_share rises (dev @1024 0.244→0.296) partly because a
merged item counts whole. `manifest.json` differs in `payload`, `oxide_source` and the
`baseline`/`protocol` labels (set by hand).

Command: `uv run --locked python tests/python/contextbench.py run --out fixtures/eval/contextbench-py/baseline-v20`
(under a 200% CPU / 3 GB cap). v19 is recoverable at `c35d67c`.
