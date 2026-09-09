#!/usr/bin/env bash
# OXIDE perf harness: measures cold index, no-change reindex, single-file edit,
# search/context latency, peak RSS, and DB size on a deterministic synthetic repo.
# Usage: scripts/perf.sh [modules_per_lang]
set -euo pipefail

# Argument is either a module count (synthetic repo, the default) or a path to
# an existing repository, which is copied to $WORK and measured in place. The
# copy matters: these runs write a .oxide/ index and edit one file, and doing
# that to a real checkout in situ would be rude.
N="${1:-200}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/target/release/oxide"
OFFLINE_EMBEDDER=(env -u OXIDE_EMBED_URL -u OXIDE_EMBED_MODEL OXIDE_EMBED_NATIVE=hashed)
WORK="${TMPDIR:-/tmp}/oxide-perf-$$"

trap 'rm -rf "$WORK"' EXIT

if [ -d "$N" ]; then
  mkdir -p "$WORK"
  cp -r "$N" "$WORK/repo"
  rm -rf "$WORK/repo/.oxide"
  echo "== repo: $N =="
else
  python3 "$ROOT/scripts/gen_bench_repo.py" "$WORK/repo" "$N"
  echo "== repo: $N modules/lang =="
fi

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
cold_rss=$(run_rss "$cold_out" "${OFFLINE_EMBEDDER[@]}" "$BIN" index .)
cold=$(grep '^took' "$cold_out" | grep -o '[0-9]*')
rm -f "$cold_out"

warm_out="$(mktemp)"
warm_rss=$(run_rss "$warm_out" "${OFFLINE_EMBEDDER[@]}" "$BIN" index .)
warm=$(grep '^took' "$warm_out" | grep -o '[0-9]*')
rm -f "$warm_out"

# touch exactly one file: the synthetic repo's known service module when it is
# there, otherwise the largest indexable source file in the repo (a comment
# appended at the end, so the edit is real but changes no semantics).
python3 - <<'EOF'
import pathlib
p = pathlib.Path("src/py/service_7/svc.py")
if p.exists():
    s = p.read_text()
    s = s.replace('result = {"module": 7,', 'result = {"module": 707,')
    assert '707' in s, "edit did not apply"
    p.write_text(s)
else:
    # A new declaration, not just a comment: a trailing comment changes no
    # symbol's span, so nothing re-embeds and the measurement is vacuous.
    exts = {
        ".py": "\ndef perf_edit_probe():\n    return 1\n",
        ".ts": "\nexport function perfEditProbe(): number {\n  return 1;\n}\n",
        ".tsx": "\nexport function perfEditProbe(): number {\n  return 1;\n}\n",
        ".rs": "\npub fn perf_edit_probe() -> u32 {\n    1\n}\n",
        ".go": "\nfunc perfEditProbe() int {\n\treturn 1\n}\n",
    }
    cands = [f for f in pathlib.Path(".").rglob("*")
             if f.is_file() and f.suffix in exts and ".oxide" not in f.parts]
    assert cands, "no indexable file to edit"
    target = max(cands, key=lambda f: f.stat().st_size)
    target.write_text(target.read_text() + exts[target.suffix])
    print(f"edited {target}")
EOF
edit_out_file="$(mktemp)"
edit_rss=$(run_rss "$edit_out_file" "${OFFLINE_EMBEDDER[@]}" "$BIN" index .)
edit_ms=$(grep '^took' "$edit_out_file" | grep -o '[0-9]*')
embed_line=$(grep 'symbols:' "$edit_out_file")
rm -f "$edit_out_file"

# search latency: best of 3 hybrid searches
lat=$(for i in 1 2 3; do
  /usr/bin/time -f "%e" "${OFFLINE_EMBEDDER[@]}" "$BIN" search "retry policy should_retry attempts" --limit 5 2>&1 >/dev/null | tail -1
done | sort -n | head -1)

# context latency: best of 3
ctx_lat=$(for i in 1 2 3; do
  /usr/bin/time -f "%e" "${OFFLINE_EMBEDDER[@]}" "$BIN" context --task "fix retry policy backoff" --budget-tokens 4096 2>&1 >/dev/null | tail -1
done | sort -n | head -1)

size=$(du -h .oxide/index.db | cut -f1)
files_count=$("${OFFLINE_EMBEDDER[@]}" "$BIN" stats | awk '/^files:/ {print $2}')
symbols_count=$("${OFFLINE_EMBEDDER[@]}" "$BIN" stats | awk '/^symbols:/ {print $2}')

printf 'files=%s symbols=%s\n' "$files_count" "$symbols_count"
printf 'cold_index=%sms (peak_rss=%sKB) warm_index=%sms (peak_rss=%sKB) single_edit=%sms (peak_rss=%sKB) (%s)\n' \
  "$cold" "$cold_rss" "$warm" "$warm_rss" "$edit_ms" "$edit_rss" "$embed_line"
printf 'search_latency_best_of_3=%ss context_latency_best_of_3=%ss db_size=%s\n' "$lat" "$ctx_lat" "$size"
