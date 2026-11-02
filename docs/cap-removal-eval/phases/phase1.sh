#!/usr/bin/env bash
# #43 phase 1 pilot: frozen held-out pipeline restricted to httpx/requests/flask, scored descriptively.
set -eu
J=~/.cache/oxide-capremoval-eval; R=/home/khoi/Projects/oxide; PY=~/.cache/oxide-typed-eval/venv/bin/python
bash /tmp/claude-1000/-home-khoi-Projects-oxide/6037cda2-95b0-4329-9466-d9899710274a/scratchpad/pilot_heldout.sh
cd $R && $PY docs/cap-removal-eval/scripts/build_tasks.py heldout
mkdir -p $J/pilot/tasks && mv $J/tasks/heldout-frozen.jsonl $J/pilot/tasks/heldout-frozen.jsonl
mv $J/tasks/heldout-draws.jsonl $J/pilot/tasks/heldout-draws.jsonl
: > $J/pilot/tasks/cb-frozen.jsonl
echo PILOT-TASKS $(wc -l < $J/pilot/tasks/heldout-frozen.jsonl)
$PY /tmp/claude-1000/-home-khoi-Projects-oxide/6037cda2-95b0-4329-9466-d9899710274a/scratchpad/phase1_run.py
echo PHASE1-DONE
