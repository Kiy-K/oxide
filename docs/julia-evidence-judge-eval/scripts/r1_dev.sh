#!/usr/bin/env bash
# R1 (ch 8) for each form on every dev set; usage: r1_dev.sh J1 [J2 J3...]
J=~/.cache/oxide-julia-eval
for f in "$@"; do for s in cb heldout dev bcd ca c5; do
  $J/run_j.sh $J/tasks/$s.jsonl $s-R1$f --modes balanced --challengers 8 --julia $J/scores/$s.$f.jsonl --timing-reps 1
done; done
echo R1-DONE
