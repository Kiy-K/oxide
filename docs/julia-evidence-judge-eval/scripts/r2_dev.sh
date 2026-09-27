#!/usr/bin/env bash
J=~/.cache/oxide-julia-eval
for t in 0.05 0.1 0.2 0.3 0.5; do for s in cb heldout dev; do
  $J/run_j.sh $J/tasks/$s.jsonl $s-R2J2-$t --modes balanced --challengers 9 --julia $J/scores/$s.J2.jsonl --tau $t --timing-reps 1
done; done
$J/r1_dev.sh RND
echo R2-DONE
