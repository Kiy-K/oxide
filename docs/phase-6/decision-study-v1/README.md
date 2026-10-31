# Decision Study v1: REJECT raw probability mapping

Historical study, 2026-10-24; [preregistration](preregistration.md), engine seam at `fef45e5`. **Dev gate FAIL; held-out unopened.** JEV `jev-1.14.0` had ranking signal, but its probabilities did not fit the heuristic's admission scale. No production change.

Reported Python dev (15 tasks, 707 candidates): compact renderer within-task AUROC 0.878 versus heuristic 0.716; no-rank 0.861. LR@4096 fell 0.659→0.593: only 70 candidates cleared the 0.4 floor (heuristic 512), using 1,348 versus 2,709 units. Compact fit every request; canonical/shared exceeded context on 8/15 and 6/15. Operational repeat/reversal recall drift also missed the ±0.010 tolerance. [gate-dev.txt](results/gate-dev.txt) records all gates and controls.

**Post hoc, not promotable:** keeping heuristic admission and ordering by `0.4 + 0.59p` reached LR@4096 0.783 versus random same-set order 0.583 and oracle 0.792, including the rank-ablated variant. Gain came from five tasks, no regressions at 4096; selected after observing failure, so it required [v2](../decision-study-v2/README.md), not integration. See [posthoc-dev.txt](results/posthoc-dev.txt).

Costs: 120 authorized requests, 1,521,222 billed input tokens, $0.064 total; compact median 0.90 s, ~15,110 tokens/$0.00063 per request. Three overlong dev queries lost no candidate-specific tail evidence; this does not establish harmless clipping on held-out. The model manifest, renderers, deadline selfcheck, controls, truncation audit and scripts remain retained.

## Reproduce

Corpora as graph-v3; private on-disk `TMPDIR`, `TIKTOKEN_CACHE_DIR`; one job under `systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200%`.

```sh
S=docs/phase-6/decision-study-v1/scripts
O=~/Projects/oxide-eval-data/oxide-decision-study-v1/dev-controls
uv run --with tiktoken python $S/study.py $O
uv run python $S/adapter.py selfcheck
# Live calls require new source-disclosure/spend authorization and TYPESAFE_API_KEY:
uv run python $S/adapter.py run $O compact compact --authorize <corpora>
uv run --with tiktoken python $S/study.py $O/scored-study NAME=$O/judgments/NAME.json ...
python $S/gate.py $O/scored-study
uv run --with tiktoken python $S/posthoc.py $O/scored-posthoc NAME=$O/judgments/NAME.json ...
python $S/gate.py $O/scored-posthoc
```

Recorded judgments/raw responses live outside git in `$O/judgments` and `$O/raw`, alongside source-bearing request files. Committed reports alone do not provide full offline replay without those files.
