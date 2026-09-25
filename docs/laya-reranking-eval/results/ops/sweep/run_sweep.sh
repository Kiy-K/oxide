#!/usr/bin/env bash
# Sequential CPU sweep (issue #15 gate O). One config at a time; nothing else benchmarking.
set -u
cd "$(dirname "$0")/../../.." || exit 1
PY=~/.cache/oxide-laya-eval/venv/bin/python
CK=~/.cache/oxide-laya-eval/ckpt-55cf4c4e
IN=results/inputs/cb-shortlists-first3.jsonl
O=results/ops/sweep
P1=0,2,4,6,8,10          # one hyper-thread per P-core
P12=0-11                 # all P-core hyper-threads
export USE_TF=0
SWEEP_THREADS=6  $PY -I scripts/cpu_sweep.py torch $CK $IN $O/ref-torch-t6-unpinned.json
REF=$O/ref-torch-t6-unpinned.json
SWEEP_THREADS=6  taskset -c $P1  $PY -I scripts/cpu_sweep.py torch           $CK $IN $O/torch-t6-pcore.json $REF
SWEEP_THREADS=12 taskset -c $P12 $PY -I scripts/cpu_sweep.py torch           $CK $IN $O/torch-t12-pcore-ht.json $REF
SWEEP_THREADS=6  taskset -c $P1  $PY -I scripts/cpu_sweep.py torch-nocompile $CK $IN $O/torch-nocompile-t6-pcore.json $REF
SWEEP_THREADS=6  taskset -c $P1  $PY -I scripts/cpu_sweep.py torch-sorted    $CK $IN $O/torch-sorted-t6-pcore.json $REF
SWEEP_THREADS=6  taskset -c $P1  $PY -I scripts/cpu_sweep.py torch-bf16      $CK $IN $O/torch-bf16-t6-pcore.json $REF
SWEEP_THREADS=6  taskset -c $P1  $PY -I scripts/cpu_sweep.py torch-int8      $CK $IN $O/torch-int8-t6-pcore.json $REF
# ONNX: official upstream exporter (laya commit 4066d5d5), then ONNX Runtime CPU
if [ ! -f ~/.cache/oxide-laya-eval/onnx/laya-english.onnx ]; then
  mkdir -p ~/.cache/oxide-laya-eval/onnx
  $PY -I ~/.cache/oxide-laya-eval/upstream/export_onnx.py --model $CK --output ~/.cache/oxide-laya-eval/onnx/laya-english.onnx > $O/onnx-export.log 2>&1 || echo "EXPORT FAILED" >> $O/onnx-export.log
fi
if [ -f ~/.cache/oxide-laya-eval/onnx/laya-english.onnx ]; then
  SWEEP_ONNX=~/.cache/oxide-laya-eval/onnx/laya-english.onnx SWEEP_THREADS=6 taskset -c $P1 $PY -I scripts/cpu_sweep.py onnx $CK $IN $O/onnx-t6-pcore.json $REF
fi
echo SWEEP DONE
