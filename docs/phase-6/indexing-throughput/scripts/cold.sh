#!/usr/bin/env bash
# Cold-build stage profiles: RUNS fresh-store index() per corpus, one capped job at a time.
#   cold.sh OUT.jsonl SCRATCH RUNS [ARGS for profile.py] -- CORPUS...
set -euo pipefail
out=$1 scratch=$2 runs=$3
shift 3
args=()
while [[ $1 != -- ]]; do args+=("$1"); shift; done
shift
here=$(dirname "$0")
for corpus in "$@"; do
  for run in $(seq "$runs"); do
    rm -rf "$scratch/data"
    line=$(OXIDE_DATA_DIR="$scratch/data" systemd-run --user --scope -q -p MemoryMax=3G \
      -p MemorySwapMax=0 -p CPUQuota=200% uv run python "$here/profile.py" "$corpus" "${args[@]}" | tail -1)
    echo "{\"corpus\": \"$(basename "$corpus")\", \"run\": $run, \"args\": \"${args[*]}\", \"result\": $line}" >>"$out"
  done
done
rm -rf "$scratch/data"
