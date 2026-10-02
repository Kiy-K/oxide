#!/usr/bin/env bash
# Packs the @oxide/native addon that `mise run native:build` built into the
# release asset `oxide-native-<version>-x86_64-unknown-linux-gnu.tar.gz` (#36
# P1), after checking the artifact itself. npm-pack layout, so a consumer can
# `npm install` the file directly; nothing is published to npm.
#
# Usage: scripts/native_package.sh <version> <out-dir>   (version: v0.2.0)
#
# Prints `asset=<path>`, `glibc=<floor>` and `glibcxx=<floor>` lines for the
# release workflow's outputs and job summary.
set -euo pipefail

version="${1:?usage: native_package.sh <version> <out-dir>}"
out="${2:?usage: native_package.sh <version> <out-dir>}"
target="x86_64-unknown-linux-gnu"
node_file="oxide_native.linux-x64-gnu.node"
addon="packages/native/$node_file"
[ -f "$addon" ] || { echo "no $addon: run mise run native:build" >&2; exit 1; }

# Only the system libraries a glibc Linux always has: ONNX Runtime and
# everything else must be linked in, and nothing may pin a library search
# path from the build machine.
allowed=" ld-linux-x86-64.so.2 libc.so.6 libgcc_s.so.1 libm.so.6 libstdc++.so.6 "
for lib in $(readelf -d "$addon" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p'); do
  case "$allowed" in
    *" $lib "*) ;;
    *) echo "native_package: unexpected dynamic dependency $lib" >&2; exit 1 ;;
  esac
done
if readelf -d "$addon" | grep -qE '\((RPATH|RUNPATH)\)'; then
  echo "native_package: $addon has an RPATH/RUNPATH" >&2
  exit 1
fi
glibc="$(objdump -T "$addon" | sed -n 's/.*GLIBC_\([0-9.]*\).*/\1/p' | sort -V -u | tail -1)"
glibcxx="$(objdump -T "$addon" | sed -n 's/.*GLIBCXX_\([0-9.]*\).*/\1/p' | sort -V -u | tail -1)"

stage="$(mktemp -d)"
mkdir "$stage/package"
cp packages/native/package.json packages/native/index.cjs packages/native/README.md LICENSE \
  "$addon" "$stage/package/"
# The workspace keeps 0.0.0; the asset carries the release version (npm
# semver has no leading `v`).
( cd "$stage/package" && npm pkg set "version=${version#v}" && npm pack --silent --pack-destination "$stage" >/dev/null )
mkdir -p "$out"
asset="$out/oxide-native-$version-$target.tar.gz"
mv "$stage"/oxide-native-*.tgz "$asset"
rm -rf "$stage"

echo "asset=$asset"
echo "glibc=$glibc"
echo "glibcxx=$glibcxx"
