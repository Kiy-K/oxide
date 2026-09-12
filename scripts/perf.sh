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

# Peak RSS (KB) and wall-clock ms for a command via /usr/bin/time -v, printed
# as "RSS_KB MS"; the command's own stdout is preserved in $1 (a file path) so
# callers can still parse its `--json` report.
#
# Wall clock comes from `time` rather than from OXIDE's own "in 201ms" line:
# that line is human output and has already been reformatted once (the v0.1.1
# terminal-styling pass replaced the `took Nms` this harness used to grep,
# silently breaking it), while `--json` deliberately omits the duration. An
# external clock cannot drift from either.
run_rss() {
  local outfile="$1"
  shift
  local timelog
  timelog="$(mktemp)"
  /usr/bin/time -v "$@" >"$outfile" 2>"$timelog"
  local rss elapsed
  rss="$(grep 'Maximum resident set size' "$timelog" | grep -o '[0-9]*')"
  # "0:01.23" or "1:02:03"; seconds-with-fraction is the only field that
  # varies in practice, so parse the whole thing rather than assume a shape.
  elapsed="$(grep 'Elapsed (wall clock)' "$timelog" | awk '{print $NF}')"
  rm -f "$timelog"
  printf '%s %s\n' "$rss" "$(python3 -c "
import sys
parts = [float(p) for p in sys.argv[1].split(':')]
total = 0.0
for p in parts:
    total = total * 60 + p
print(round(total * 1000))
" "$elapsed")"
}

cold_out="$(mktemp)"
read -r cold_rss cold < <(run_rss "$cold_out" "${OFFLINE_EMBEDDER[@]}" "$BIN" index . --json)
rm -f "$cold_out"

warm_out="$(mktemp)"
read -r warm_rss warm < <(run_rss "$warm_out" "${OFFLINE_EMBEDDER[@]}" "$BIN" index . --json)
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
        ".js": "\nexport function perfEditProbe() {\n  return 1;\n}\n",
        ".jsx": "\nexport function perfEditProbe() {\n  return 1;\n}\n",
        ".mjs": "\nexport function perfEditProbe() {\n  return 1;\n}\n",
        ".cjs": "\nfunction perfEditProbe() {\n  return 1;\n}\nmodule.exports.perfEditProbe = perfEditProbe;\n",
        ".java": "\nclass PerfEditProbe {\n  int run() {\n    return 1;\n  }\n}\n",
        ".rs": "\npub fn perf_edit_probe() -> u32 {\n    1\n}\n",
        ".go": "\nfunc perfEditProbe() int {\n\treturn 1\n}\n",
        ".rb": "\ndef perf_edit_probe\n  1\nend\n",
        ".php": "\nfunction perf_edit_probe(): int\n{\n    return 1;\n}\n",
        ".c": "\nint perf_edit_probe(void)\n{\n    return 1;\n}\n",
        ".cpp": "\nint perf_edit_probe()\n{\n    return 1;\n}\n",
        ".h": "\nint perf_edit_probe(void);\n",
    }
    cands = [f for f in pathlib.Path(".").rglob("*")
             if f.is_file() and f.suffix in exts and ".oxide" not in f.parts]
    assert cands, "no indexable file to edit"
    target = max(cands, key=lambda f: f.stat().st_size)
    target.write_text(target.read_text() + exts[target.suffix])
    print(f"edited {target}")
EOF
edit_out_file="$(mktemp)"
read -r edit_rss edit_ms < <(run_rss "$edit_out_file" "${OFFLINE_EMBEDDER[@]}" "$BIN" index . --json)
embed_line=$(python3 -c "
import json, sys
r = json.load(open(sys.argv[1]))
print('{new_symbols} new, {changed_symbols} changed, {embedded_symbols} written, '
      '{reused_embeddings} reused'.format(**r))
" "$edit_out_file")
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
