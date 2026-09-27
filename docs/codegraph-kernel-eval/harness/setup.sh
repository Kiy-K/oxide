#!/usr/bin/env bash
# Research-only (issue #23). Fetches the CodeGraph Kernel source at the pinned
# revision into cgk-native/vendor/ (gitignored) and the three corpus repos
# that are not already in ~/.cache/oxide-contextbench/repos into corpus-repos/.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
CODEGRAPH_SHA=dfccdf62547fcd76d343344d823a0e1998d3a89f   # colbymchenry/codegraph tag v1.6.0 (matches the npm 1.6.0 bundle)
fetch() { # dir url rev
  [ -d "$1/.git" ] && return 0
  mkdir -p "$1" && git -C "$1" init -q
  git -C "$1" fetch -q --depth 1 "$2" "$3" && git -C "$1" checkout -q FETCH_HEAD
}
fetch "$here/cgk-native/vendor/codegraph" https://github.com/colbymchenry/codegraph "$CODEGRAPH_SHA"
# The kernel's own lockfile pins every shared crate version (napi entries are
# pruned automatically by cargo since the shim does not depend on napi).
cp -n "$here/cgk-native/vendor/codegraph/codegraph-kernel/Cargo.lock" "$here/cgk-native/Cargo.lock" || true
fetch "$here/corpus-repos/gson" https://github.com/google/gson 8b4b55051489132190cb8d1c61eb9dc7f5381295
fetch "$here/corpus-repos/gin"  https://github.com/gin-gonic/gin dcaa4296d111981ffb31ac3eba90bb63e1eb5ab9
fetch "$here/corpus-repos/fmt"  https://github.com/fmtlib/fmt refs/tags/11.1.4
