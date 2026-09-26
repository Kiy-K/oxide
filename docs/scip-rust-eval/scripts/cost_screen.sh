#!/usr/bin/env bash
# Cost screen: rust-analyzer SCIP generation vs OXIDE indexing on one clap
# ContextBench base commit. Every step appends one record to $OUT via
# measure.py (wall, largest-process RSS, summed process-tree RSS).
#
#   cost_screen.sh <clap worktree> <rust-analyzer binary> <scipread binary> <out dir>
#
# The worktree must be a dedicated scratch checkout: this script deletes its
# target/ and .oxide/ and makes (then reverts) a one-line edit.
set -euo pipefail
WT=$1 RA=$2 SR=$3 OUTDIR=$4
M="$(cd "$(dirname "$0")" && pwd)/measure.py"
OXIDE="$(cd "$(dirname "$0")/../../.." && pwd)/target/release/oxide"
OUT=$OUTDIR/cost.jsonl
EDIT=src/parse/parser.rs
export RUSTUP_TOOLCHAIN=1.98.0
mkdir -p "$OUTDIR"
cd "$WT"
test -z "$(git status --porcelain -- src)" || { echo "worktree src is dirty" >&2; exit 1; }

echo '{"cargo":{"buildScripts":{"enable":false}},"procMacro":{"enable":false}}' > "$OUTDIR/ra-nobuild.json"

ra() { # label, extra args...
  local label=$1; shift
  python3 "$M" "$label" "$OUT" -- "$RA" scip . --output "$OUTDIR/$label.scip" "$@" >/dev/null
  sha256sum "$OUTDIR/$label.scip" | cut -d' ' -f1 > "$OUTDIR/$label.sha256"
}

# 1. cold, twice (determinism), then the no-build-scripts/no-proc-macro config
rm -rf target; ra ra-default-cold-1
rm -rf target; ra ra-default-cold-2
rm -rf target; ra ra-nobuild-cold --config-path "$OUTDIR/ra-nobuild.json"
# 2. warm cargo target, no edit, then after a one-line edit: a SCIP "update"
#    is a full re-run either way
rm -rf target; cargo check -q --workspace 2>/dev/null || true
ra ra-default-warm
echo "// scip-rust-eval edit probe" >> "$EDIT"
ra ra-default-after-edit
git checkout -q -- "$EDIT"

# 3. consumption: decode only, then decode + flatten to JSON
python3 "$M" scipread-stats "$OUT" -- "$SR" "$OUTDIR/ra-default-cold-1.scip" --stats-only
python3 "$M" scipread-json "$OUT" -- "$SR" "$OUTDIR/ra-default-cold-1.scip" > "$OUTDIR/ra-default-cold-1.json"

# 4. OXIDE on the same checkout: shipped native embedder and hashed
#    (structure-only, closest analogue to SCIP), cold then one-line edit
for emb in native hashed; do
  rm -rf .oxide
  if [ $emb = hashed ]; then export OXIDE_EMBED_NATIVE=hashed; else unset OXIDE_EMBED_NATIVE; fi
  python3 "$M" "oxide-$emb-cold" "$OUT" -- "$OXIDE" index . >/dev/null
  echo "// scip-rust-eval edit probe" >> "$EDIT"
  python3 "$M" "oxide-$emb-after-edit" "$OUT" -- "$OXIDE" index . >/dev/null
  git checkout -q -- "$EDIT"
  python3 "$M" "oxide-$emb-revert" "$OUT" -- "$OXIDE" index . >/dev/null
  du -sb .oxide/index.db | cut -f1 > "$OUTDIR/oxide-$emb.db.bytes"
done
unset OXIDE_EMBED_NATIVE
ls -l "$OUTDIR"/*.scip > "$OUTDIR/scip-sizes.txt"
