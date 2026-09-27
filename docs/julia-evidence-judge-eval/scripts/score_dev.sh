#!/usr/bin/env bash
J=~/.cache/oxide-julia-eval; cd $J
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 JULIA_CPU_THREADS=6
for f in J1 J2 J3; do for s in cb heldout dev bcd ca c5; do
  unshare -rn taskset -c 0,2,4,6,8,10 venv/bin/python julia_score.py dumps/$s-ch0.jsonl $f scores/$s.$f.jsonl 2>>logs/score.err && echo "done $s $f $(wc -l < scores/$s.$f.jsonl)"
done; done
echo ALL-DONE
