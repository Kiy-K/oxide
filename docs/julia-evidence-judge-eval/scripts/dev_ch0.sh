#!/usr/bin/env bash
J=~/.cache/oxide-julia-eval
for s in cb heldout dev bcd ca c5; do $J/run_j.sh $J/tasks/$s.jsonl $s-ch0 --modes balanced --challengers 0 --timing-reps 1; done
echo ALL-DONE
