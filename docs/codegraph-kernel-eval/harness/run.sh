#!/usr/bin/env bash
# Research-only (issue #23): end-to-end differential run. Idempotent; all
# output lands in work/ (gitignored); the committed evidence is copied to
# ../results/ by hand after review.
#
#   ./setup.sh && ./run.sh [REPS]       REPS = in-process timing repetitions (default 10)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"; cd "$here"
root="$(cd ../../.. && pwd)"
REPS="${1:-10}"
CPU="${BENCH_CPU:-2}"   # a P-core on the reference laptop; pin both engines to it
CG_BUNDLE="${CG_BUNDLE:-$(npm root -g)/@colbymchenry/codegraph/node_modules/@colbymchenry/codegraph-linux-x64}"
OX="$root/target/release/examples/extraction_differential"
CGK="$here/cgk-native/target/release/cgk-native"
out=work/out; mkdir -p "$out"

(cd "$root" && cargo build --release -j 2 --no-default-features --example extraction_differential)
(cd cgk-native && cargo build --release -j 2)
python3 build_corpus.py

# 1. Harness fidelity: the shim must be byte-identical to the shipped binary.
"$CG_BUNDLE/node" shipped_digest.js "$CG_BUNDLE/lib/kernel/codegraph-kernel.node" work/manifest.jsonl > "$out/digest.shipped.tsv"
"$CGK" digest work/manifest.jsonl > "$out/digest.shim.tsv"
cmp "$out/digest.shipped.tsv" "$out/digest.shim.tsv" && echo "shim == shipped kernel on $(wc -l < "$out/digest.shim.tsv") files"

# 2. Determinism: three separate processes per engine, byte-compared.
for i in 1 2 3; do
  "$OX" dump work/manifest.jsonl "$out/oxide.$i.jsonl"
  "$CGK" dump work/manifest.jsonl "$out/cgk.$i.jsonl"
done
sha256sum "$out"/oxide.?.jsonl "$out"/cgk.?.jsonl | tee "$out/determinism.sha256"

# 3. Normalized diff (raw input) and the C/C++ preParse sensitivity run.
python3 compare.py work/manifest.jsonl "$out/oxide.1.jsonl" "$out/cgk.1.jsonl" "$out/diff.json"
"$CG_BUNDLE/node" preparse.js "$CG_BUNDLE/lib/dist" work/manifest.jsonl work/manifest.preparsed.jsonl
"$CGK" dump work/manifest.preparsed.jsonl "$out/cgk_pre.1.jsonl"
python3 compare.py work/manifest.jsonl "$out/oxide.1.jsonl" "$out/cgk_pre.1.jsonl" "$out/diff_preparsed.json"

# 4. Performance: 3 fresh processes per engine, alternating, pinned, REPS each.
for i in 1 2 3; do
  taskset -c "$CPU" "$OX"  bench work/manifest.jsonl "$REPS" "$out/bench_oxide.$i.json"
  taskset -c "$CPU" "$CGK" bench work/manifest.jsonl "$REPS" "$out/bench_cgk.$i.json"
done
# Whole-process peak RSS / wall for one dump each (includes corpus load).
/usr/bin/time -v taskset -c "$CPU" "$OX"  dump work/manifest.jsonl /dev/null 2> "$out/time_oxide_dump.txt"
/usr/bin/time -v taskset -c "$CPU" "$CGK" dump work/manifest.jsonl /dev/null 2> "$out/time_cgk_dump.txt"
python3 summarize.py "$out" > "$out/summary.md"
echo "done: $out/summary.md"
