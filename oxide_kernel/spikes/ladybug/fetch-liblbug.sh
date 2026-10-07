#!/usr/bin/env bash
# Fetch the pinned, sha256-checked prebuilt liblbug and print the env that
# makes lbug's build.rs link it ("external" mode). This replaces lbug's
# default, which runs a downloader script from LadybugDB/ladybug@main and
# links the *latest* release (README § Build).
set -euo pipefail
version=0.21.2
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)
    asset=liblbug-static-linux-x86_64-compat.tar.gz
    sha=60147b2ae28c092d8f895f1c23913766be8df477c379c2bc57d86ecada0a6a3f ;;
  *) echo "no pinned liblbug for $(uname -s)-$(uname -m); add its asset digest here" >&2; exit 1 ;;
esac
dir="$(cd "$(dirname "$0")" && pwd)/.cache/liblbug-$version"
if [ ! -f "$dir/liblbug.a" ]; then
  mkdir -p "$dir"
  curl -fsSL -o "$dir/$asset" "https://github.com/LadybugDB/ladybug/releases/download/v$version/$asset"
  echo "$sha  $dir/$asset" | sha256sum -c - >&2
  tar -xzf "$dir/$asset" -C "$dir"
  rm "$dir/$asset"
fi
# FTS/vector are not in liblbug: they are shared objects that `INSTALL`
# downloads at runtime, unsigned and keyed by the extension version (0.21.0
# for every 0.21.x). Pin our own digests and LOAD them by path instead.
ext_version=0.21.0
ext="$dir/extensions"
for pin in fts:742f2756c2f80bcd1886b6ee0446483038cff0ecf6cf47f698bc6fe855cbaef6 \
           vector:571726cc2ea0d202df1909ae5a3b0528c9dea3ea8d40be6c16b310ec14aa8821; do
  name=${pin%%:*} file="$ext/lib${pin%%:*}.lbug_extension"
  if [ ! -f "$file" ]; then
    mkdir -p "$ext"
    curl -fsSL -o "$file.tmp" "https://extension.ladybugdb.com/v$ext_version/linux_amd64/$name/lib$name.lbug_extension"
    echo "${pin#*:}  $file.tmp" | sha256sum -c - >&2
    mv "$file.tmp" "$file"
  fi
done
echo "export LBUG_LIBRARY_DIR=$dir LBUG_INCLUDE_DIR=$dir SPIKE_EXTENSION_DIR=$ext"
