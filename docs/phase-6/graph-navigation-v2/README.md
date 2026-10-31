# Graph navigation v2: narrow dispatch fix accepted; no new interface

Historical recheck, 2026-10-25, against `b7f7959`. **Keep ADR-0013 Proposed.** [Original completeness failure](../graph-navigation/README.md) reproduced exactly (Q1 recall/F1 0.659/0.701, Q2 0.585/0.633). The [AGC trials](../agc/README.md) did not justify general exposure.

**Resolver correction accepted by maintainer:** attribute assignments block dispatch only on the relevant receiver's class hierarchy, not globally by method name. `response.request = req` had incorrectly blocked `Session.request`. All recovered edges remain heuristic; routing ignores them. Python v5→v6, facts v3→v4; [baseline-v14](../baseline-v14/README.md) and [typescript-v11](../typescript-v11/README.md) record identity-only amendments.

Reported acceptance results across flask, requests, pytest, xarray and seaborn: 74 edges added, none removed or found incorrect. Frozen navigation Q1 unchanged; Q2 recall 0.585→0.597, F1 0.633→0.639. Thirty-six of 44 original misses were unknown receiver types, untouched by the fix. Five interleaved fresh-process/store index repetitions showed +0.2–4.9% median time, at most +2.4 MB RSS; pytest's +74 ms was consistent, requests within ~±5% spread.

**Correction history:** disabling the global block suggested 82 recovered edges on the first three corpora, but the narrow rule recovered 53. The probe had also disabled a separate module-global rebinding guard; that guard was retained. The original uncommitted-stage verification had identity-only failures, not quality regressions; subsequent freezes are linked above. No new agent runs or non-Python dispatch evaluation.

Evidence: [attrs_gain.json](results/attrs_gain.json), [fix_gain.json](results/fix_gain.json), [score-fix.txt](results/score-fix.txt), [index_time.json](results/index_time.json), and scripts. Source witnesses, distance and provenance already existed; they are not breakage or completeness claims. Current contracts live in [public-api.md](../../public-api.md), not this historical report.

## Reproduce

```sh
S=docs/phase-6/graph-navigation-v2/scripts
for c in flask requests pytest; do for m in base noattrs; do
  uv run python $S/attrs_probe.py $m $c; done; done
git worktree add --detach /path/to/head-wt b7f7959
for c in xarray seaborn; do
  PYTHONPATH=/path/to/head-wt/src uv run python $S/attrs_probe.py head $c
  uv run python $S/attrs_probe.py fix $c; done
uv run python $S/index_time.py /path/to/head-wt/src src
uv run python $S/score.py ~/Projects/oxide-eval-data/oxide-gnav2-eval/data-base
uv run python $S/score.py ~/Projects/oxide-eval-data/oxide-gnav2-eval/data-noattrs
```

Use the recorded historical implementation for `base`/`fix` comparisons; `base` meant HEAD at the time, not current HEAD.
