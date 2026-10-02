#!/usr/bin/env bash
# Tests the packed @oxide/native release asset the way a consumer gets it
# (#36 P1): npm-installed into a clean directory outside this checkout, run
# with no Rust toolchain on PATH, never rebuilt.
#
# Usage: scripts/native_smoke.sh <oxide-native-*.tar.gz> <oxide-binary>
#
# Needs this checkout's @oxide/protocol and @oxide/client built (their dist/
# is packed here as the clean install's client; they are not release assets)
# and Node/npm/pnpm on PATH. Checks, in order:
#   1. the committed client integration suite, unchanged, over the native
#      and process backends (the binary is the process baseline);
#   2. the addon mapped into the process is the installed file;
#   3. the shipped default embedder loads through the native backend;
#   4. a process-only install (optional dependencies omitted) still works,
#      and asking it for the native backend fails cleanly.
set -euo pipefail

asset="$(realpath "${1:?usage: native_smoke.sh <native-asset> <oxide-binary>}")"
binary="$(realpath "${2:?usage: native_smoke.sh <native-asset> <oxide-binary>}")"
checkout="$PWD"
for package in protocol client; do
  [ -f "packages/$package/dist/index.js" ] || {
    echo "native_smoke: build @oxide/$package first (pnpm exec turbo run build)" >&2
    exit 1
  }
done

work="$(mktemp -d)"
mkdir "$work/dl"
for package in protocol client; do
  ( cd "packages/$package" && pnpm pack --pack-destination "$work/dl" >/dev/null )
done

# The committed suite runs unchanged: the install mirrors the checkout's
# layout, so its relative fixture path resolves, and `@oxide/client`
# resolves from node_modules instead of the workspace.
install_with() {
  local dir="$1"
  shift
  mkdir -p "$dir/packages/client/test/integration" "$dir/fixtures"
  printf '{"name":"oxide-native-smoke","private":true,"type":"module"}\n' > "$dir/package.json"
  ( cd "$dir" && npm install --no-audit --no-fund --silent "$@" )
  cp "$checkout"/packages/client/test/integration/*.ts "$dir/packages/client/test/integration/"
  cp -R "$checkout/fixtures/py_repo" "$dir/fixtures/py_repo"
}

# Node and the system tools only: no cargo or rustc can be reached.
clean_path="$(dirname "$(command -v node)"):/usr/bin:/bin"
if env PATH="$clean_path" sh -c 'command -v cargo || command -v rustc' >/dev/null; then
  echo "native_smoke: a Rust toolchain is reachable on the clean PATH ($clean_path)" >&2
  exit 1
fi
in_install() {
  local dir="$1"
  shift
  ( cd "$dir" && env -i HOME="$HOME" PATH="$clean_path" OXIDE_BIN="$binary" "$@" )
}

full="$work/install"
install_with "$full" "$work"/dl/oxide-protocol-*.tgz "$work"/dl/oxide-client-*.tgz "$asset"
echo "== 1. integration suite (native + process) from the clean install"
in_install "$full" node --test packages/client/test/integration/native.test.ts \
  packages/client/test/integration/binary.test.ts

echo "== 2. the addon is loaded from the install"
# shellcheck disable=SC2016 # `${...}` below is a JS template literal, not shell.
in_install "$full" node --input-type=module -e '
import { readFileSync } from "node:fs";
import { Oxide } from "@oxide/client";
await new Oxide({ cwd: "/nonexistent", backend: "native" }).status().catch(() => {});
const mapped = [...new Set(readFileSync("/proc/self/maps", "utf8").split("\n")
  .map((line) => line.split(/\s+/).slice(5).join(" ")).filter((p) => p.endsWith(".node")))];
const expected = `${process.cwd()}/node_modules/@oxide/native/oxide_native.linux-x64-gnu.node`;
if (mapped.length !== 1 || mapped[0] !== expected) {
  console.error("mapped addons:", mapped, "expected:", expected);
  process.exit(1);
}
console.log("loaded", mapped[0]);
'

echo "== 3. the shipped default embedder through the native backend"
in_install "$full" node --input-type=module -e '
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Oxide } from "@oxide/client";
const repo = mkdtempSync(join(tmpdir(), "oxide-native-default-"));
mkdirSync(join(repo, "src"));
writeFileSync(join(repo, "src/app.py"),
  "def handler():\n    return authenticate()\n\ndef authenticate():\n    return True\n");
const oxide = new Oxide({ cwd: repo, backend: "native" });
await oxide.index();
const status = await oxide.status();
if (!status.is_current || status.embedder !== "native:arctic-embed-xs-q") {
  console.error("unexpected status:", status);
  process.exit(1);
}
const pack = await oxide.query("where is authentication handled");
if (pack.items.length === 0) process.exit(1);
console.log("default embedder", status.embedder, "items", pack.items.length);
'

echo "== 4. process-only install (optional dependencies omitted)"
process_only="$work/process-only"
install_with "$process_only" --omit=optional "$work"/dl/oxide-protocol-*.tgz "$work"/dl/oxide-client-*.tgz
[ ! -e "$process_only/node_modules/@oxide/native" ] || {
  echo "native_smoke: --omit=optional still installed @oxide/native" >&2
  exit 1
}
in_install "$process_only" node --test packages/client/test/integration/binary.test.ts
in_install "$process_only" node --input-type=module -e '
import { Oxide, OxideClientError } from "@oxide/client";
try {
  new Oxide({ cwd: "/tmp", backend: "native" });
} catch (error) {
  if (error instanceof OxideClientError && error.reason === "native") {
    console.log("native backend without the addon:", error.reason);
    process.exit(0);
  }
  throw error;
}
console.error("the native backend loaded without the addon");
process.exit(1);
'

rm -rf "$work"
echo "native smoke passed"
