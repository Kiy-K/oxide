# baseline-v17: whitespace-adjacent views merged

Payload `oxide-payload-v4`: views of one file separated only by whitespace render as one
item under one heading (v3 merged only overlapping views). Selection is unchanged.

Against baseline-v16 (same tasks, gold audit, scorer, budgets, corpora, tolerance), verified:
dev file_recall@1024 0.447→0.437 and @4096 0.467→0.457, span/line recall unchanged;
heldout span_recall@256 0.018→0.036, @1024 0.123→0.126, @4096 0.203→0.210, line_recall
up at every budget, file_recall@256 0.252→0.249. `manifest.json` differs in `payload`
and `oxide_source`.

Command: `OXIDE_CONTEXTBENCH_TS=1 uv run --locked python tests/python/contextbench_ts.py run --out fixtures/eval/contextbench-ts/baseline-v17`
(under a 200% CPU / 3 GB cap). v16 is recoverable at `c35d67c`.
