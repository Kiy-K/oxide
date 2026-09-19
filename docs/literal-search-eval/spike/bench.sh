#!/usr/bin/env bash
# Driver for this spike crate's `bench` binary. Run from anywhere; builds
# the release binary and runs it against one or more repos. Relies on the
# binary's own /proc/self/status VmHWM report for peak RSS rather than
# wrapping it externally with `/usr/bin/time -v`, which isn't guaranteed to
# exist.
#
# Usage: docs/literal-search-eval/spike/bench.sh [REPO...]
# With no arguments, runs against: OXIDE's two committed fixtures, the
# OXIDE repository itself, and two synthetic corpora generated on the fly.
set -euo pipefail

SPIKE="$(cd "$(dirname "$0")" && pwd)"
OXIDE_ROOT="$(cd "$SPIKE/../../.." && pwd)"
REPS="${REPS:-15}"

cargo build --release -j 2 --manifest-path "$SPIKE/Cargo.toml"
BIN="$SPIKE/target/release/bench"

if [ "$#" -gt 0 ]; then
  REPS="$REPS" "$BIN" "$@"
  exit 0
fi

WORK="${TMPDIR:-/tmp}/oxide-literal-trigram-bench-$$"
mkdir -p "$WORK"
python3 "$OXIDE_ROOT/scripts/gen_bench_repo.py" "$WORK/repo200" 200 >/dev/null
python3 "$OXIDE_ROOT/scripts/gen_bench_repo.py" "$WORK/repo800" 800 >/dev/null
(cd "$WORK/repo200" && git init -q)
(cd "$WORK/repo800" && git init -q)

echo "== OXIDE fixtures + OXIDE repo + synthetic corpora, REPS=$REPS =="
REPS="$REPS" "$BIN" \
  "$OXIDE_ROOT/fixtures/py_repo" \
  "$OXIDE_ROOT/fixtures/ts_repo" \
  "$OXIDE_ROOT" \
  "$WORK/repo200" \
  "$WORK/repo800"

echo "== control-only session (for RSS isolation) =="
REPS="$REPS" SKIP_TRIGRAM=1 "$BIN" "$OXIDE_ROOT"

echo "(synthetic corpora left at $WORK; remove manually when done)"
