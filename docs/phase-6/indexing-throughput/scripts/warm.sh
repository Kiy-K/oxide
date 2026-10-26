#!/usr/bin/env bash
# Edit-one-file re-index profiles (today's parse-fact reuse), one capped job at a time.
#   warm.sh OUT.jsonl SCRATCH RUNS -- CORPUS...
# Each run: copy the corpus into SCRATCH, cold index, append a function to the median
# source file, profile the re-index; also records the generation and graph.lbug sizes
# and the time to copy graph.lbug (the floor of a copy-on-write delta publication),
# the cold profile, and digest.py of both generations. PYTHONPATH selects the code.
set -euo pipefail
out=$1 scratch=$2 runs=$3
shift 4
here=$(dirname "$0")
caps=(systemd-run --user --scope -q -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200%)
for corpus in "$@"; do
  for run in $(seq "$runs"); do
    rm -rf "$scratch/data" "$scratch/repo"
    cp -r "$corpus" "$scratch/repo"
    cold=$(OXIDE_DATA_DIR="$scratch/data" "${caps[@]}" uv run python "$here/profile.py" "$scratch/repo" | tail -1)
    cold_digest=$(uv run python "$here/digest.py" "$scratch/data")
    gen=$(ls -d "$scratch"/data/repos/*/generations/* | head -1)
    file=$(find "$scratch/repo" -type f \( -name '*.py' -o -name '*.ts' -o -name '*.go' -o -name '*.rs' \) | sort | awk '{a[NR]=$0} END {print a[int(NR/2)]}')
    case $file in
      *.py) printf '\n\ndef _bench_added():\n    return 1\n' >>"$file" ;;
      *.ts) printf '\nexport function benchAdded(): number { return 1; }\n' >>"$file" ;;
      *.go) printf '\nfunc benchAdded() int { return 1 }\n' >>"$file" ;;
      *.rs) printf '\npub fn bench_added() -> i32 { 1 }\n' >>"$file" ;;
    esac
    gen_mb=$(du -sm "$gen" | cut -f1) graph_mb=$(du -sm "$gen/graph.lbug" | cut -f1)
    start=$(date +%s.%N); cp "$gen/graph.lbug" "$scratch/copy.lbug"; sync "$scratch/copy.lbug"
    copy_s=$(printf "%.3f" "$(echo "$(date +%s.%N) - $start" | bc)"); rm -f "$scratch/copy.lbug"
    line=$(OXIDE_DATA_DIR="$scratch/data" "${caps[@]}" uv run python "$here/profile.py" "$scratch/repo" | tail -1)
    warm_digest=$(uv run python "$here/digest.py" "$scratch/data")
    echo "{\"corpus\": \"$(basename "$corpus")\", \"run\": $run, \"generation_mb\": $gen_mb, \"graph_mb\": $graph_mb, \"graph_copy_s\": $copy_s, \"result\": $line, \"cold\": $cold, \"cold_digest\": $cold_digest, \"warm_digest\": $warm_digest}" >>"$out"
  done
done
rm -rf "$scratch/data" "$scratch/repo"
