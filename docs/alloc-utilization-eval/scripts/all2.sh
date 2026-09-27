#!/usr/bin/env bash
E=~/.cache/oxide-alloc-eval; T=~/.cache/oxide-intent-eval/tasks
$E/run.sh $T/cb.jsonl cb --modes fast,balanced,quality --challengers 0,1,2,3,4,5,6
$E/run.sh $T/heldout-parent.jsonl heldout --modes fast,balanced,quality --challengers 0,1,2,3,4,5,6
$E/run.sh $T/cb.jsonl cb-blast --modes balanced --challengers 0,1,5,6 --blast
$E/run.sh $T/heldout-parent.jsonl heldout-blast --modes balanced --challengers 0,1,5,6 --blast
$E/run.sh $T/dev.jsonl dev --modes balanced --challengers 0,1,3,5,6
$E/run.sh $T/bcd.jsonl bcd --modes balanced --challengers 0,1,3,5,6
$E/run.sh $T/ca.jsonl ca --modes balanced --challengers 0,1,3,5,6
