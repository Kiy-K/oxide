#!/usr/bin/env bash
# Stage 0 (protocol §10): both checkpoints, all three questions, P-core pinned.
set -u
cd "$(dirname "$0")/../../.." || exit 1
PY=~/.cache/oxide-laya-eval/venv/bin/python
CK=~/.cache/oxide-laya-eval/ckpt-55cf4c4e
export USE_TF=0
taskset -c 0,2,4,6,8,10 $PY -I scripts/laya_score.py score $CK              results/inputs/stage0-shortlists.jsonl results/quality/stage0/scores-english.jsonl 6
taskset -c 0,2,4,6,8,10 $PY -I scripts/laya_score.py score $CK/multilingual results/inputs/stage0-shortlists.jsonl results/quality/stage0/scores-multilingual.jsonl 6
echo STAGE0_DONE
