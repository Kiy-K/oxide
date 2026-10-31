---
name: oxide-eval-freeze
description: Run, freeze and commit an OXIDE phase-6 evaluation milestone (before/after context-quality artifacts) for a language corpus. Use when asked to "freeze the eval", "run the gate against baseline", "record before/after metrics", "finish the <language> milestone", or "commit the eval artifacts" in the oxide repo.
---

# OXIDE eval milestone freeze

Work in `~/Projects/oxide` (branch is usually `rewrite/v2`). Eval data and scratch live outside the repo in `~/Projects/oxide-eval-data/oxide-<lang>-eval/` (or `~/.cache/oxide-<lang>-eval/`).

## 1. Always run heavy work under resource caps

Every eval, probe, pytest or verify run uses this prefix:

```bash
export TMPDIR=<eval-dir>/tmp
systemd-run --user --scope -q -p CPUQuota=200% -p MemoryMax=3G -p MemorySwapMax=0 uv run --locked <command>
```

Never run them uncapped or with the system `/tmp`. Write long output to a log in `<eval-dir>` and print only `tail`/`grep` summaries.

## 2. Produce both arms

- `before`: the frozen baseline (previous milestone or `git archive` of the baseline commit), never regenerated with new code.
- `after`: the current tree.

Each arm must contain `metrics.json`, `performance.json`, `results.json`, and `manifest.json` or `gold-audit.json`.

## 3. Freeze into docs

```bash
mkdir -p docs/phase-6/<lang>-vN/before
cp <after-dir>/*.json docs/phase-6/<lang>-vN/
cp <before-dir>/*.json docs/phase-6/<lang>-vN/before/
```

Check that the manifests record `oxide_source` (commit, plus `+dirty` if applicable), `baseline` and component versions. Check that default index failures are 0 in both arms.

## 4. Validate and compute gated deltas

```bash
uv run --locked python - <<'EOF'
import json, sys
sys.path.insert(0, "tests/python")
import contextbench as cb
b = json.load(open("docs/phase-6/<lang>-vN/before/metrics.json"))
a = json.load(open("docs/phase-6/<lang>-vN/metrics.json"))
for s in ("dev", "heldout"):
    print(s, {m: round(a[s]["verified"][m] - b[s]["verified"][m], 4) for m in cb.GATED})
print("tolerance", cb.TOLERANCE)
EOF
```

Any gated metric that drops by more than `TOLERANCE` is a failed gate. Report it and stop, without rationalising it away. Recount numbers yourself (spans, tasks, audit classes) rather than trusting summaries from another agent.

## 5. Write the milestone README

Write `docs/phase-6/<lang>-vN/README.md` with the corpora and revisions, the gated deltas table, tasks/spans counts, performance, any oracle results (mark provisional ones as provisional), and an exact reproduce block with the capped commands above.

## 6. Clean up, verify, commit

- Remove scratch venvs, `dist/` and stray generated JSON from the eval dir. Never delete anything under `src/` or earlier milestones.
- Run `mise run verify` under the caps, writing to `<eval-dir>/verify-precommit.log`, and require rc=0.
- Run `git status --short` and `git diff --stat`. Confirm that large files match the precedent of earlier milestones (they also commit full `results.json`).
- Commit with a message naming the milestone and the gate result.
