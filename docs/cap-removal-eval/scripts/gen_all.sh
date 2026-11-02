#!/usr/bin/env bash
# #43 fresh task generation, in order: exclusions at the frozen commit, held-out, ContextBench.
# usage: gen_all.sh <frozen_commit>
set -eu
FROZEN=$1
J=~/.cache/oxide-capremoval-eval; J40=~/.cache/oxide-jev-eval
H=$(cd "$(dirname "$0")" && pwd); R=$(cd "$H/../../.." && pwd)
PY=~/.cache/oxide-typed-eval/venv/bin/python   # numpy + pyarrow
mkdir -p $J/tasks $J/logs $J/excl
[ -e $J/repos ] || ln -s $J40/repos $J/repos
[ -e $J/cb-repos ] || ln -s $J40/cb-repos $J/cb-repos
[ -f $J/excl/exclusions.json ] || python3 $R/docs/jev-typed-evidence-eval/scripts/excl.py $FROZEN $J40/repos $J/excl > $J/logs/excl.log
bash $H/gen_heldout.sh
[ -f $J/tasks/heldout-frozen.jsonl ] || $PY $H/build_tasks.py heldout
[ -f $J/tasks/cb-frozen.jsonl ] || $PY $H/build_tasks.py cb $J40/contextbench-full.parquet
echo GEN-ALL-DONE
