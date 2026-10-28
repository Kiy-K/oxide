#!/usr/bin/env bash
# Interleaved HEAD vs working-tree A/B through indexing-throughput's warm.sh (cold + one-file
# warm index, stage profile, row and BM25 digests, generation and graph.lbug sizes).
#   ab.sh OUTDIR HEAD_SRC ROUNDS -- CORPUS...
set -euo pipefail
out=$1 head=$2 rounds=$3
shift 4
warm=$(dirname "$0")/../../indexing-throughput/scripts/warm.sh
mkdir -p "$out/scratch"
for round in $(seq "$rounds"); do
  for corpus in "$@"; do
    PYTHONPATH="$head" "$warm" "$out/head.jsonl" "$out/scratch" 1 -- "$corpus"
    "$warm" "$out/new.jsonl" "$out/scratch" 1 -- "$corpus"
  done
done
