#!/usr/bin/env bash
# Explicit Linux x86_64 native dependency preparation. No runtime downloads.
set -euo pipefail
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) ;;
  *) echo 'OXIDE Phase 2B supports Linux x86_64 only' >&2; exit 1 ;;
esac
base="$(cd "$(dirname "$0")" && pwd)/.cache"
version=0.21.2
asset=liblbug-static-linux-x86_64-compat.tar.gz
sha=60147b2ae28c092d8f895f1c23913766be8df477c379c2bc57d86ecada0a6a3f
mkdir -p "$base"
exec 9>"$base/prepare.lock"
flock 9
archive="$base/$asset"
if [ ! -f "$archive" ]; then
  curl --fail --location --retry 3 --output "$archive.tmp" "https://github.com/LadybugDB/ladybug/releases/download/v$version/$asset"
  echo "$sha  $archive.tmp" | sha256sum --check -
  mv "$archive.tmp" "$archive"
fi
echo "$sha  $archive" | sha256sum --check -
# Reconstruct the controlled paths from the verified archive on every prepare.
# A corrupt or modified extracted cache is never trusted.
dir="$base/liblbug-$version"
mkdir -p "$dir"
tar -xzf "$archive" -C "$dir"
# FTS is a separate native extension (ADR-0002 § 3): never `INSTALL`ed at
# runtime. Fetch the pinned file once, verify it here, and the runtime
# re-verifies the digest before `LOAD EXTENSION` by path.
ext_version=0.21.0
fts_sha=742f2756c2f80bcd1886b6ee0446483038cff0ecf6cf47f698bc6fe855cbaef6
fts="$base/extensions-$ext_version/libfts.lbug_extension"
if [ ! -f "$fts" ]; then
  mkdir -p "$(dirname "$fts")"
  curl --fail --location --retry 3 --output "$fts.tmp" "https://extension.ladybugdb.com/v$ext_version/linux_amd64/fts/libfts.lbug_extension"
  echo "$fts_sha  $fts.tmp" | sha256sum --check -
  mv "$fts.tmp" "$fts"
fi
echo "$fts_sha  $fts" | sha256sum --check -
