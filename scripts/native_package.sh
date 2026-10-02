#!/usr/bin/env bash
# Packs the @oxide/native addon that `mise run native:build` built for this
# host into the release asset `oxide-native-<version>-<target>.tar.gz` (#36
# P1/P2), after checking the artifact itself. npm-pack layout, so a consumer
# can `npm install` the file directly; nothing is published to npm.
#
# Usage: scripts/native_package.sh <version> <out-dir>   (version: v0.2.0)
#
# Prints `asset=<path>`, `requires=<system requirements>` and
# `deps=<dynamic dependencies>` lines for the release workflow's outputs and
# job summary.
set -euo pipefail

version="${1:?usage: native_package.sh <version> <out-dir>}"
out="${2:?usage: native_package.sh <version> <out-dir>}"
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) platform="linux-x64-gnu" target="x86_64-unknown-linux-gnu" ;;
  Darwin-arm64) platform="darwin-arm64" target="aarch64-apple-darwin" ;;
  *) echo "native_package: no @oxide/native build for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac
node_file="oxide_native.$platform.node"
addon="packages/native/$node_file"
[ -f "$addon" ] || { echo "no $addon: run mise run native:build" >&2; exit 1; }
fail() { echo "native_package: $*" >&2; exit 1; }

# Only libraries the OS always has: ONNX Runtime and everything else must be
# linked in, and nothing may pin a library search path from the build machine.
case "$platform" in
  linux-*)
    deps="$(readelf -d "$addon" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p')"
    allowed=" ld-linux-x86-64.so.2 libc.so.6 libgcc_s.so.1 libm.so.6 libstdc++.so.6 "
    for lib in $deps; do
      case "$allowed" in *" $lib "*) ;; *) fail "unexpected dynamic dependency $lib" ;; esac
    done
    ! readelf -d "$addon" | grep -qE '\((RPATH|RUNPATH)\)' || fail "$addon has an RPATH/RUNPATH"
    glibc="$(objdump -T "$addon" | sed -n 's/.*GLIBC_\([0-9.]*\).*/\1/p' | sort -V -u | tail -1)"
    glibcxx="$(objdump -T "$addon" | sed -n 's/.*GLIBCXX_\([0-9.]*\).*/\1/p' | sort -V -u | tail -1)"
    requires="glibc >= $glibc, libstdc++ GLIBCXX >= $glibcxx"
    ;;
  darwin-*)
    # `otool -L` lists the dylib's own install name first; skip it.
    id="$(otool -D "$addon" | tail -n +2)"
    deps="$(otool -L "$addon" | tail -n +2 | awk '{print $1}' | grep -vxF "$id" || true)"
    for lib in $deps; do
      case "$lib" in /usr/lib/* | /System/Library/*) ;; *) fail "unexpected dynamic dependency $lib" ;; esac
    done
    ! otool -l "$addon" | grep -q LC_RPATH || fail "$addon has an LC_RPATH"
    [ "$(lipo -archs "$addon")" = "arm64" ] || fail "$addon is not arm64-only: $(lipo -archs "$addon")"
    minos="$(otool -l "$addon" | awk '/LC_BUILD_VERSION/ { found = 1 } found && $1 == "minos" { print $2; exit }')"
    [ -n "$minos" ] || fail "$addon has no LC_BUILD_VERSION minos"
    requires="macOS >= $minos (arm64)"
    ;;
esac

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
echo "requires=$requires"
echo "deps=$(echo "$deps" | xargs)"
