# baseline-v23: the crowding gate counts bodies, not items

Selector `oxide-select-greedy-v6`, the single amendment over `oxide-select-greedy-v4`
(baseline-v21). A query-sensitive test down-weight (`TEST_WEIGHT = 0.5`) applies only
when the first packed bundle leaves a production **symbol** without its own body
(reduced to a header on its turn, or omitted for budget) while test bodies occupy more
budget units than production bodies. Units are attributed by complete entities, not
whole items, so a method packed beside its class header in one merged item still counts
its body toward production. File outlines and prerequisite headers never count as
losing room. The down-weighted retry is kept only if it strictly increases production
units; it can never push a candidate below the admit floor, so test-only queries keep
their test evidence. Ties break symbol-before-file (then pool position). Derivation
unchanged (`sha256:e71d99ef…`); only `versions.selector` differs from v21.

Against baseline-v21 (`oxide-select-greedy-v4`, the superseded directory removed in this
change), 23 of 44 tasks changed at the `results.json` level; no gated verified metric
fell past the 0.02 tolerance. Verified splits:
- dev: entry metrics identical (`entry_recall@5` 0.608, `@20` 0.701, `mrr` 0.584,
  `routed_coverage` 0.950; `span@{1024,4096}` 0.508/0.623, `line@{1024,4096}`
  0.548/0.659 unchanged); `span_recall@256` 0.080→0.147, `line_recall@256` 0.193→0.251,
  `file_recall@1024` 0.711→0.778.
- heldout: `span_recall@1024` 0.109→0.118, `span_recall@4096` 0.169→0.240,
  `line_recall@4096` 0.233→0.297, `file_recall@256` 0.270→0.322,
  `file_recall@1024` 0.529→0.632.

Unverified splits (not gated) fall at 4096 where the kept retry spreads budget over more
items: heldout unverified `line_recall@4096` 0.284→0.259, `file_recall@4096` 0.734→0.672;
all unverified `line_recall@4096` 0.292→0.270, `file_recall@4096` 0.776→0.724. The only
test-path gold lost is `pydata__xarray-6599` (`xarray/tests/test_computation.py`,
unverified): partial→missed @1024, complete→missed @4096 — the retry admits production
bodies ahead of an unverified test span. `pytest-dev__pytest-10051` gains a verified span
(logging.py \[345,350] missed→partial @1024); `pytest-dev__pytest-7571` gains four complete
spans @4096 (three verified; two from missed, two from partial). 0 verified production
regressions.

Command: `uv run --locked python tests/python/contextbench.py run --out fixtures/eval/contextbench-py/baseline-v23`
(under a 200% CPU / 3 GB cap). `manifest.json` `baseline`/`protocol` labels set by hand.
`oxide_source` is `e883f93… +dirty` (the base commit; the working-tree change is this
selector amendment). v21 is recoverable at `e883f93:fixtures/eval/contextbench-py/baseline-v21`.
