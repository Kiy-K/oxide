#!/usr/bin/env bash
# Stage 1 (protocol §10): selected english/noul_ab on full dev, plain then masked.
set -u
cd "$(dirname "$0")/../../.." || exit 1
PY=~/.cache/oxide-laya-eval/venv/bin/python
CK=~/.cache/oxide-laya-eval/ckpt-55cf4c4e
export USE_TF=0
for R in plain masked; do
  taskset -c 0,2,4,6,8,10 $PY -I scripts/laya_score.py score $CK results/inputs/dev-$R-shortlists.jsonl results/quality/stage1/scores-english-noul-$R.jsonl 6 noul_ab
done
echo STAGE1_DONE
