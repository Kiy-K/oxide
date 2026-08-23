#!/usr/bin/env bash
# OXIDE perf harness: measures cold index, no-change reindex, single-file edit,
# search latency, and DB size on a deterministic synthetic repo.
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

cold=$("$BIN" index . | grep '^took' | grep -o '[0-9]*')
warm=$("$BIN" index . | grep '^took' | grep -o '[0-9]*')

# touch exactly one function body in one file
python3 - <<'EOF'
import pathlib
p = pathlib.Path("src/py/service_7/svc.py")
s = p.read_text()
s = s.replace('result = {"module": 7,', 'result = {"module": 707,')
assert '707' in s, "edit did not apply"
p.write_text(s)
EOF
edit_out=$("$BIN" index .)
edit_ms=$(echo "$edit_out" | grep '^took' | grep -o '[0-9]*')
embed_line=$(echo "$edit_out" | grep 'symbols:')

# search latency: best of 3 hybrid searches
lat=$(for i in 1 2 3; do
  /usr/bin/time -f "%e" "$BIN" search "retry policy should_retry attempts" --limit 5 2>&1 >/dev/null | tail -1
done | sort -n | head -1)

size=$(du -h .oxide/index.db | cut -f1)
files_count=$("$BIN" stats | awk '/^files:/ {print $2}')
symbols_count=$("$BIN" stats | awk '/^symbols:/ {print $2}')

printf 'files=%s symbols=%s\n' "$files_count" "$symbols_count"
printf 'cold_index=%sms warm_index=%sms single_edit=%sms (%s)\n' "$cold" "$warm" "$edit_ms" "$embed_line"
printf 'search_latency_best_of_3=%ss db_size=%s\n' "$lat" "$size"
