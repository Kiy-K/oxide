#!/usr/bin/env bash
# OXIDE perf harness: measures cold index, no-change reindex, single-file edit,
# search/context latency, peak RSS, and DB size on a deterministic synthetic repo.
# Usage: scripts/perf.sh [modules_per_lang]
set -euo pipefail

N="${1:-200}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/target/release/oxide"
WORK="${TMPDIR:-/tmp}/oxide-perf-$$"

trap 'rm -rf "$WORK"' EXIT

python3 "$ROOT/scripts/gen_bench_repo.py" "$WORK/repo" "$N"
echo "== repo: $N modules/lang =="

cd "$WORK/repo"

# Peak RSS (KB) for a command via /usr/bin/time -v; command's own stdout is
# preserved in $1 (a file path) so callers can still parse "took Nms" etc.
run_rss() {
  local outfile="$1"
  shift
  local timelog
  timelog="$(mktemp)"
  /usr/bin/time -v "$@" >"$outfile" 2>"$timelog"
  grep 'Maximum resident set size' "$timelog" | grep -o '[0-9]*'
  rm -f "$timelog"
}

cold_out="$(mktemp)"
cold_rss=$(run_rss "$cold_out" "$BIN" index .)
cold=$(grep '^took' "$cold_out" | grep -o '[0-9]*')
rm -f "$cold_out"

warm_out="$(mktemp)"
warm_rss=$(run_rss "$warm_out" "$BIN" index .)
warm=$(grep '^took' "$warm_out" | grep -o '[0-9]*')
rm -f "$warm_out"

# touch exactly one function body in one file
python3 - <<'EOF'
import pathlib
p = pathlib.Path("src/py/service_7/svc.py")
s = p.read_text()
s = s.replace('result = {"module": 7,', 'result = {"module": 707,')
assert '707' in s, "edit did not apply"
p.write_text(s)
EOF
edit_out_file="$(mktemp)"
edit_rss=$(run_rss "$edit_out_file" "$BIN" index .)
edit_ms=$(grep '^took' "$edit_out_file" | grep -o '[0-9]*')
embed_line=$(grep 'symbols:' "$edit_out_file")
rm -f "$edit_out_file"

# search latency: best of 3 hybrid searches
lat=$(for i in 1 2 3; do
  /usr/bin/time -f "%e" "$BIN" search "retry policy should_retry attempts" --limit 5 2>&1 >/dev/null | tail -1
done | sort -n | head -1)

# context latency: best of 3
ctx_lat=$(for i in 1 2 3; do
  /usr/bin/time -f "%e" "$BIN" context --task "fix retry policy backoff" --budget-tokens 4096 2>&1 >/dev/null | tail -1
done | sort -n | head -1)

size=$(du -h .oxide/index.db | cut -f1)
files_count=$("$BIN" stats | awk '/^files:/ {print $2}')
symbols_count=$("$BIN" stats | awk '/^symbols:/ {print $2}')

printf 'files=%s symbols=%s\n' "$files_count" "$symbols_count"
printf 'cold_index=%sms (peak_rss=%sKB) warm_index=%sms (peak_rss=%sKB) single_edit=%sms (peak_rss=%sKB) (%s)\n' \
  "$cold" "$cold_rss" "$warm" "$warm_rss" "$edit_ms" "$edit_rss" "$embed_line"
printf 'search_latency_best_of_3=%ss context_latency_best_of_3=%ss db_size=%s\n' "$lat" "$ctx_lat" "$size"
