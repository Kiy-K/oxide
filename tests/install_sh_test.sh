#!/bin/sh
# Lifecycle tests for the public installer (`install.sh`), run against a
# local release directory served over `file://`.
#
#   ./tests/install_sh_test.sh
#
# By default it packages whatever is in `target/release/oxide` into a
# throwaway release. In CI it is pointed at the archives the release
# workflow just built:
#
#   OXIDE_TEST_RELEASE_DIR=/path/to/assets ./tests/install_sh_test.sh
#
# The point of testing here rather than only in the workflow is that
# `install.sh` is the thing users actually run, and most of its work happens
# on failure paths — a bad checksum, a missing asset, a machine with no
# SHA-256 tool — that a successful release build never exercises.

set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALLER="$ROOT/install.sh"
[ -f "$INSTALLER" ] || { echo "no install.sh at $INSTALLER" >&2; exit 1; }

SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/oxide-install-test.XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT HUP INT TERM

PASS=0
FAIL=0

ok() {
    PASS=$((PASS + 1))
    printf '  ok   %s\n' "$*"
}

bad() {
    FAIL=$((FAIL + 1))
    printf '  FAIL %s\n' "$*"
}

check() {
    # check DESCRIPTION CONDITION...
    description="$1"
    shift
    if "$@"; then ok "$description"; else bad "$description"; fi
}

contains() {
    # contains FILE SUBSTRING
    grep -qF -- "$2" "$1"
}

case_name() { printf '\n%s\n' "$*"; }

# --- build a throwaway release -------------------------------------------

TARGET_OS="$(uname -s)"
TARGET_ARCH="$(uname -m)"
case "$TARGET_OS" in
    Linux) TARGET_OS_PART="unknown-linux-gnu" ;;
    Darwin) TARGET_OS_PART="apple-darwin" ;;
    *) echo "these tests only run on Linux or macOS" >&2; exit 1 ;;
esac
case "$TARGET_ARCH" in
    x86_64 | amd64) TARGET_ARCH_PART="x86_64" ;;
    aarch64 | arm64) TARGET_ARCH_PART="aarch64" ;;
    *) echo "unsupported test architecture $TARGET_ARCH" >&2; exit 1 ;;
esac
TARGET="$TARGET_ARCH_PART-$TARGET_OS_PART"

SOURCE_BIN=""
if [ -n "${OXIDE_TEST_RELEASE_DIR:-}" ]; then
    # Unpack the archive the release workflow built for this host, so the
    # binary under test is the one that would actually ship.
    unpack="$SANDBOX/from-release"
    mkdir -p "$unpack"
    found=""
    for archive in "$OXIDE_TEST_RELEASE_DIR"/*"$TARGET".tar.gz; do
        [ -f "$archive" ] || continue
        tar -xzf "$archive" -C "$unpack"
        found="$archive"
        break
    done
    [ -n "$found" ] || { echo "no archive for $TARGET in $OXIDE_TEST_RELEASE_DIR" >&2; exit 1; }
    SOURCE_BIN="$unpack/oxide"
else
    SOURCE_BIN="$ROOT/target/release/oxide"
    [ -x "$SOURCE_BIN" ] || {
        echo "no release binary at $SOURCE_BIN — run: cargo build --release" >&2
        exit 1
    }
fi
echo "Testing install.sh with $TARGET binary: $SOURCE_BIN"

OLD_VERSION="v9.9.8"
NEW_VERSION="v9.9.9"
RELEASE="$SANDBOX/release"
mkdir -p "$RELEASE"

package() {
    # package VERSION [BINARY]
    version="$1"
    binary="${2:-$SOURCE_BIN}"
    stage="$SANDBOX/stage-$version"
    rm -rf "$stage"
    mkdir -p "$stage"
    cp "$binary" "$stage/oxide"
    chmod 755 "$stage/oxide"
    tar -czf "$RELEASE/oxide-$version-$TARGET.tar.gz" -C "$stage" .
}

package "$OLD_VERSION"
package "$NEW_VERSION"

rewrite_sums() {
    ( cd "$RELEASE" && sha256sum ./*.tar.gz 2>/dev/null || shasum -a 256 ./*.tar.gz ) |
        sed 's| \./| |' > "$RELEASE/SHA256SUMS"
}
rewrite_sums

BASE_URL="file://$RELEASE"

install_oxide() {
    # install_oxide DEST_DIR VERSION [extra args...] -> writes $SANDBOX/out
    dest="$1"
    version="$2"
    shift 2
    OXIDE_BASE_URL="$BASE_URL" sh "$INSTALLER" \
        --version "$version" --install-dir "$dest" "$@" > "$SANDBOX/out" 2>&1
}

# --- 1. clean first install ----------------------------------------------

case_name "clean first install"
DEST="$SANDBOX/bin"
if install_oxide "$DEST" "$OLD_VERSION"; then
    ok "installer exits 0"
else
    bad "installer exits 0"
    cat "$SANDBOX/out"
fi
check "binary lands at the default name" test -x "$DEST/oxide"
check "reports the version it installed" sh -c "'$DEST/oxide' --version >/dev/null"
check "checksum verification is reported" contains "$SANDBOX/out" "Checksum verified"
check "points at \`oxide install\` next" contains "$SANDBOX/out" "oxide install"

# --- 2. explicit version install -----------------------------------------

case_name "explicit version install"
DEST2="$SANDBOX/bin-explicit"
install_oxide "$DEST2" "$NEW_VERSION" || bad "explicit version install exits 0"
check "installs the requested version" contains "$SANDBOX/out" "$NEW_VERSION"
check "version without a leading v is accepted" \
    sh -c "OXIDE_BASE_URL='$BASE_URL' sh '$INSTALLER' --version 9.9.9 --install-dir '$DEST2' >/dev/null 2>&1"

# --- 3. upgrade in place -------------------------------------------------

case_name "upgrade in place"
before_inode="$(ls -i "$DEST/oxide" | cut -d' ' -f1)"
install_oxide "$DEST" "$NEW_VERSION" || bad "upgrade exits 0"
after_inode="$(ls -i "$DEST/oxide" | cut -d' ' -f1)"
check "binary is replaced, not merely re-run" test "$before_inode" != "$after_inode"
check "still executable after upgrade" test -x "$DEST/oxide"
check "no staging file left behind" \
    sh -c "! ls '$DEST'/.oxide.new.* >/dev/null 2>&1"

# --- 4. reinstall the same version ---------------------------------------

case_name "reinstall the same version"
if install_oxide "$DEST" "$NEW_VERSION"; then
    ok "reinstall is not an error"
else
    bad "reinstall is not an error"
    cat "$SANDBOX/out"
fi
check "binary still works" sh -c "'$DEST/oxide' --version >/dev/null"

# --- 5. invalid version --------------------------------------------------

case_name "invalid version"
cp "$DEST/oxide" "$SANDBOX/known-good"
if install_oxide "$DEST" "v0.0.0-nope"; then
    bad "a missing release must fail"
else
    ok "a missing release fails"
fi
check "says which asset was missing" contains "$SANDBOX/out" "could not download"
check "leaves the working binary in place" cmp -s "$DEST/oxide" "$SANDBOX/known-good"

# --- 6. unsupported architecture -----------------------------------------

case_name "unsupported architecture"
SHIM="$SANDBOX/shim"
mkdir -p "$SHIM"
cat > "$SHIM/uname" <<'SHIM_EOF'
#!/bin/sh
case "${1:-}" in
    -m) echo "sparc64" ;;
    -s) echo "Linux" ;;
    *) echo "Linux" ;;
esac
SHIM_EOF
chmod +x "$SHIM/uname"
if PATH="$SHIM:$PATH" OXIDE_BASE_URL="$BASE_URL" sh "$INSTALLER" \
    --version "$NEW_VERSION" --install-dir "$DEST" > "$SANDBOX/out" 2>&1; then
    bad "an unsupported architecture must fail"
else
    ok "an unsupported architecture fails"
fi
check "names the architecture it saw" contains "$SANDBOX/out" "sparc64"
check "says which architectures exist" contains "$SANDBOX/out" "x86_64"

cat > "$SHIM/uname" <<'SHIM_EOF'
#!/bin/sh
case "${1:-}" in
    -m) echo "x86_64" ;;
    *) echo "Plan9" ;;
esac
SHIM_EOF
chmod +x "$SHIM/uname"
if PATH="$SHIM:$PATH" OXIDE_BASE_URL="$BASE_URL" sh "$INSTALLER" \
    --version "$NEW_VERSION" --install-dir "$DEST" > "$SANDBOX/out" 2>&1; then
    bad "an unsupported OS must fail"
else
    ok "an unsupported OS fails"
fi
check "names the operating system it saw" contains "$SANDBOX/out" "Plan9"

# --- 7. failed download (asset present, checksums absent) ----------------

case_name "missing checksum file"
EMPTY="$SANDBOX/no-sums"
mkdir -p "$EMPTY"
cp "$RELEASE/oxide-$NEW_VERSION-$TARGET.tar.gz" "$EMPTY/"
if OXIDE_BASE_URL="file://$EMPTY" sh "$INSTALLER" \
    --version "$NEW_VERSION" --install-dir "$DEST" > "$SANDBOX/out" 2>&1; then
    bad "a missing SHA256SUMS must fail"
else
    ok "a missing SHA256SUMS fails"
fi
check "refuses to install unverified" contains "$SANDBOX/out" "unverified"
check "leaves the working binary in place" cmp -s "$DEST/oxide" "$SANDBOX/known-good"

case_name "asset not listed in SHA256SUMS"
PARTIAL="$SANDBOX/partial-sums"
mkdir -p "$PARTIAL"
cp "$RELEASE/oxide-$NEW_VERSION-$TARGET.tar.gz" "$PARTIAL/"
echo "0000000000000000000000000000000000000000000000000000000000000000  something-else.tar.gz" \
    > "$PARTIAL/SHA256SUMS"
if OXIDE_BASE_URL="file://$PARTIAL" sh "$INSTALLER" \
    --version "$NEW_VERSION" --install-dir "$DEST" > "$SANDBOX/out" 2>&1; then
    bad "an unlisted asset must fail"
else
    ok "an unlisted asset fails"
fi
check "says the asset is not listed" contains "$SANDBOX/out" "does not list"

# --- 8. checksum mismatch ------------------------------------------------

case_name "checksum mismatch"
TAMPERED="$SANDBOX/tampered"
mkdir -p "$TAMPERED"
cp "$RELEASE/SHA256SUMS" "$TAMPERED/"
cp "$RELEASE/oxide-$NEW_VERSION-$TARGET.tar.gz" "$TAMPERED/"
# Same name, different bytes: exactly the substitution the checksum exists
# to catch.
printf 'not a tarball' >> "$TAMPERED/oxide-$NEW_VERSION-$TARGET.tar.gz"
if OXIDE_BASE_URL="file://$TAMPERED" sh "$INSTALLER" \
    --version "$NEW_VERSION" --install-dir "$DEST" > "$SANDBOX/out" 2>&1; then
    bad "a checksum mismatch must fail"
else
    ok "a checksum mismatch fails"
fi
check "says checksum mismatch" contains "$SANDBOX/out" "checksum mismatch"
check "prints both checksums" contains "$SANDBOX/out" "expected"
check "leaves the working binary in place" cmp -s "$DEST/oxide" "$SANDBOX/known-good"
check "unpacks nothing on mismatch" \
    sh -c "! ls '${TMPDIR:-/tmp}'/oxide-install.*/unpacked >/dev/null 2>&1"

# --- 9. missing checksum utility -----------------------------------------

case_name "no SHA-256 tool available"
MINIMAL="$SANDBOX/minimal-path"
mkdir -p "$MINIMAL"
for tool in sh env uname mktemp curl wget tar cp chmod mv rm sed cut head tail mkdir cat ls grep; do
    real="$(command -v "$tool" 2>/dev/null || true)"
    [ -n "$real" ] && ln -sf "$real" "$MINIMAL/$tool"
done
# Deliberately not linked: sha256sum, shasum, openssl.
if PATH="$MINIMAL" OXIDE_BASE_URL="$BASE_URL" sh "$INSTALLER" \
    --version "$NEW_VERSION" --install-dir "$DEST" > "$SANDBOX/out" 2>&1; then
    bad "no checksum tool must fail"
else
    ok "no checksum tool fails"
fi
check "names the tools it needs" contains "$SANDBOX/out" "sha256sum"
check "refuses to install unverified" contains "$SANDBOX/out" "unverified"
check "leaves the working binary in place" cmp -s "$DEST/oxide" "$SANDBOX/known-good"

# --- 10. destination with spaces -----------------------------------------

case_name "destination containing spaces"
SPACED="$SANDBOX/my tools/local bin"
if install_oxide "$SPACED" "$NEW_VERSION"; then
    ok "installs into a path with spaces"
else
    bad "installs into a path with spaces"
    cat "$SANDBOX/out"
fi
check "binary is there and runs" sh -c "'$SPACED/oxide' --version >/dev/null"

# --- 11. destination not on PATH -----------------------------------------

case_name "destination not on PATH"
OFFPATH="$SANDBOX/nowhere-near-path"
install_oxide "$OFFPATH" "$NEW_VERSION" || bad "off-PATH install exits 0"
check "warns the directory is not on PATH" contains "$SANDBOX/out" "not on your PATH"
check "shows the export line to add" contains "$SANDBOX/out" "export PATH="

case_name "destination already on PATH"
ONPATH="$SANDBOX/on-path"
mkdir -p "$ONPATH"
PATH="$ONPATH:$PATH" OXIDE_BASE_URL="$BASE_URL" sh "$INSTALLER" \
    --version "$NEW_VERSION" --install-dir "$ONPATH" > "$SANDBOX/out" 2>&1 ||
    bad "on-PATH install exits 0"
check "does not warn about PATH" sh -c "! grep -qF 'not on your PATH' '$SANDBOX/out'"

# --- 12. a binary that cannot run leaves the old one alone ---------------

case_name "downloaded binary does not run"
BROKEN="$SANDBOX/broken"
mkdir -p "$BROKEN/stage"
printf '#!/nonexistent/interpreter\n' > "$BROKEN/stage/oxide"
chmod 755 "$BROKEN/stage/oxide"
tar -czf "$BROKEN/oxide-$NEW_VERSION-$TARGET.tar.gz" -C "$BROKEN/stage" .
( cd "$BROKEN" && { sha256sum ./*.tar.gz 2>/dev/null || shasum -a 256 ./*.tar.gz; } |
    sed 's| \./| |' > SHA256SUMS )
if OXIDE_BASE_URL="file://$BROKEN" sh "$INSTALLER" \
    --version "$NEW_VERSION" --install-dir "$DEST" > "$SANDBOX/out" 2>&1; then
    bad "an unrunnable binary must fail"
else
    ok "an unrunnable binary fails"
fi
check "says the existing install was left alone" contains "$SANDBOX/out" "left alone"
check "previous binary is untouched" cmp -s "$DEST/oxide" "$SANDBOX/known-good"
check "previous binary still runs" sh -c "'$DEST/oxide' --version >/dev/null"

# --- 13. the lifecycle property `oxide install` depends on ---------------
#
# `oxide install` writes the binary's absolute path into each coding agent's
# MCP config. An upgrade must therefore land on that same path, or every
# agent silently points at a binary that is gone. This is the case the CLI
# phase surfaced and could not close on its own.

case_name "upgrade keeps agent MCP configuration valid"
LIFE="$SANDBOX/lifecycle"
LIFE_BIN="$LIFE/bin"
LIFE_HOME="$LIFE/home"
mkdir -p "$LIFE_HOME/.codex"
printf 'model = "gpt-5"\n\n[tui]\ntheme = "dark"\n' > "$LIFE_HOME/.codex/config.toml"

install_oxide "$LIFE_BIN" "$OLD_VERSION" || bad "lifecycle install exits 0"
RECORDED_PATH="$LIFE_BIN/oxide"
check "installed at the path agents will record" test -x "$RECORDED_PATH"

HOME="$LIFE_HOME" "$RECORDED_PATH" install --agent codex --yes > "$SANDBOX/out" 2>&1 ||
    bad "oxide install --agent codex succeeds"
check "MCP config records the absolute install path" \
    contains "$LIFE_HOME/.codex/config.toml" "$RECORDED_PATH"
cp "$LIFE_HOME/.codex/config.toml" "$SANDBOX/config-before"
before_inode="$(ls -i "$RECORDED_PATH" | cut -d' ' -f1)"

install_oxide "$LIFE_BIN" "$NEW_VERSION" || bad "lifecycle upgrade exits 0"
after_inode="$(ls -i "$RECORDED_PATH" | cut -d' ' -f1)"

check "the upgrade actually replaced the binary" test "$before_inode" != "$after_inode"
check "MCP config is byte-identical after the upgrade" \
    cmp -s "$LIFE_HOME/.codex/config.toml" "$SANDBOX/config-before"
check "the recorded path still exists and is executable" test -x "$RECORDED_PATH"

# The real proof: speak MCP to the binary at the path the agent recorded.
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"lifecycle-test","version":"1"}}}' |
    "$RECORDED_PATH" mcp > "$SANDBOX/mcp-out" 2>/dev/null || true
check "the recorded path serves MCP after the upgrade" \
    contains "$SANDBOX/mcp-out" '"serverInfo"'
check "it identifies itself as oxide" contains "$SANDBOX/mcp-out" '"name":"oxide"'

# And uninstall still finds its own entry through the upgraded binary.
HOME="$LIFE_HOME" "$RECORDED_PATH" uninstall --agent codex --yes > "$SANDBOX/out" 2>&1 ||
    bad "oxide uninstall --agent codex succeeds"
check "uninstall removes the entry it wrote" \
    sh -c "! grep -qF 'mcp_servers.oxide' '$LIFE_HOME/.codex/config.toml'"
check "unrelated agent settings survive" \
    contains "$LIFE_HOME/.codex/config.toml" 'theme = "dark"'

# --- summary --------------------------------------------------------------

printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
