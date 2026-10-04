#!/usr/bin/env bash
# #43 phases 2-3 (unattended): wait for the pilot, then the frozen full pipeline to the verdict.
set -eu
while pgrep -f "scratchpad/phase1.sh" >/dev/null; do sleep 20; done
cd /home/khoi/Projects/oxide
PY=~/.cache/oxide-typed-eval/venv/bin/python
echo "=== phase 2-3 start $(date -Is) ==="
bash docs/cap-removal-eval/scripts/gen_all.sh b400c13
echo "TASKS heldout=$(wc -l < ~/.cache/oxide-capremoval-eval/tasks/heldout-frozen.jsonl) cb=$(wc -l < ~/.cache/oxide-capremoval-eval/tasks/cb-frozen.jsonl)"
$PY docs/cap-removal-eval/scripts/run.py parity
$PY docs/cap-removal-eval/scripts/run.py arms
$PY docs/cap-removal-eval/scripts/run.py rss
$PY docs/cap-removal-eval/scripts/score.py
echo "PHASE23-DONE $(date -Is)"
