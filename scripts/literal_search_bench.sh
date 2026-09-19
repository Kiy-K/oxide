#!/usr/bin/env bash
# Benchmarks `oxide search --mode literal` (the native byte-substring scan,
# src/literal.rs) against `rg -F` restricted to the same file set, on a
# deterministic synthetic repo. This is issue #6's control-arm measurement:
# a trigram/FTS5 index is only worth adding if it beats this scan by enough
# to justify its size, so this script's numbers are the baseline any such
# proposal has to clear.
#
# Usage: scripts/literal_search_bench.sh [modules_per_lang] [reps]
set -euo pipefail

N="${1:-200}"
REPS="${2:-20}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/target/release/oxide"
WORK="${TMPDIR:-/tmp}/oxide-literal-bench-$$"

trap 'rm -rf "$WORK"' EXIT

if [ ! -x "$BIN" ]; then
  echo "error: $BIN not found; run 'cargo build --release' first" >&2
  exit 1
fi
if ! command -v rg >/dev/null 2>&1; then
  echo "error: rg (ripgrep) not found; required for the comparison" >&2
  exit 1
fi

python3 "$ROOT/scripts/gen_bench_repo.py" "$WORK/repo" "$N"
cd "$WORK/repo"
git init -q
echo "== synthetic repo: $N modules/lang, $REPS reps per tool =="

# A pattern guaranteed present (from gen_bench_repo.py's template) so both
# tools do real matching work, not a fruitless scan.
PATTERN="RetryPolicy"

now_ms() { python3 -c 'import time; print(int(time.time()*1000))'; }

percentile() {
  # $1: percentile (0-100), stdin: one integer ms per line
  python3 -c "
import sys
samples = sorted(int(x) for x in sys.stdin if x.strip())
p = $1 / 100.0
idx = min(len(samples) - 1, max(0, int(round(p * (len(samples) - 1)))))
print(samples[idx])
"
}

run_reps() {
  # remaining args: the command to time, $REPS times
  local samples=()
  for _ in $(seq 1 "$REPS"); do
    local start end
    start="$(now_ms)"
    "$@" >/dev/null 2>&1
    end="$(now_ms)"
    samples+=("$((end - start))")
  done
  printf '%s\n' "${samples[@]}"
}

echo "-- oxide search --mode literal (native scan, no index) --"
oxide_samples="$(run_reps "$BIN" search "$PATTERN" --mode literal --json --limit 200)"
oxide_p50="$(echo "$oxide_samples" | percentile 50)"
oxide_p95="$(echo "$oxide_samples" | percentile 95)"
oxide_hits="$("$BIN" search "$PATTERN" --mode literal --json --limit 200 | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["hits"]))')"
printf 'p50=%sms p95=%sms hits(capped at 200)=%s\n' "$oxide_p50" "$oxide_p95" "$oxide_hits"

echo "-- rg -F (whole tree, its own default ignore policy) --"
rg_samples="$(run_reps rg -F "$PATTERN" .)"
rg_p50="$(echo "$rg_samples" | percentile 50)"
rg_p95="$(echo "$rg_samples" | percentile 95)"
rg_hits="$(rg -F -c "$PATTERN" . 2>/dev/null | awk -F: '{sum+=$2} END{print sum+0}')"
printf 'p50=%sms p95=%sms hits(uncapped)=%s\n' "$rg_p50" "$rg_p95" "$rg_hits"

echo "-- cold-index cost (for comparison; literal search never needs this) --"
index_start="$(now_ms)"
env -u OXIDE_EMBED_URL -u OXIDE_EMBED_MODEL OXIDE_EMBED_NATIVE=hashed "$BIN" index . >/dev/null
index_end="$(now_ms)"
printf 'oxide index (one-time, unrelated to literal search): %sms\n' "$((index_end - index_start))"
du -sh .oxide 2>/dev/null | awk '{print "index size: " $1}'
