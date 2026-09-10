#!/bin/sh
# OXIDE installer.
#
#   curl -fsSL https://raw.githubusercontent.com/Kiy-K/oxide/main/install.sh | sh
#
# Installs the `oxide` binary. It does NOT configure coding agents — that is
# `oxide install`, a different thing, run after this one.
#
# POSIX sh, no bashisms. Downloads a prebuilt archive from a GitHub release,
# verifies it against the release's published SHA256SUMS before anything
# executable is unpacked, and moves it into place atomically so a failure at
# any step leaves an existing installation working.

set -eu

REPO="${OXIDE_REPO:-Kiy-K/oxide}"
BIN="oxide"

usage() {
    cat <<EOF
Install the OXIDE binary.

USAGE
  install.sh [options]

OPTIONS
  --version VERSION      Release to install, e.g. v0.1.0 (default: latest)
  --install-dir DIR      Where to put the binary (default: \$HOME/.local/bin)
  --help                 Show this message

ENVIRONMENT
  OXIDE_VERSION          Same as --version
  OXIDE_INSTALL_DIR      Same as --install-dir
  OXIDE_REPO             GitHub repository to install from (default: $REPO)
  OXIDE_BASE_URL         Base URL holding the release assets, for a mirror or
                         an offline copy. Requires --version.

After installing, connect OXIDE to your coding agents with:
  oxide install
EOF
}

say() { printf '%s\n' "$*"; }
err() { printf 'install.sh: %s\n' "$*" >&2; }

die() {
    err "$*"
    exit 1
}

need() {
    command -v "$1" >/dev/null 2>&1 || die "\`$1\` is required but was not found on PATH"
}

VERSION="${OXIDE_VERSION:-}"
INSTALL_DIR="${OXIDE_INSTALL_DIR:-}"
BASE_URL="${OXIDE_BASE_URL:-}"

while [ $# -gt 0 ]; do
    case "$1" in
        --version)
            [ $# -ge 2 ] || die "--version needs a value, e.g. --version v0.1.0"
            VERSION="$2"
            shift 2
            ;;
        --version=*)
            VERSION="${1#--version=}"
            shift
            ;;
        --install-dir)
            [ $# -ge 2 ] || die "--install-dir needs a value"
            INSTALL_DIR="$2"
            shift 2
            ;;
        --install-dir=*)
            INSTALL_DIR="${1#--install-dir=}"
            shift
            ;;
        --help | -h)
            usage
            exit 0
            ;;
        *)
            err "unknown option: $1"
            usage >&2
            exit 2
            ;;
    esac
done

[ -n "$INSTALL_DIR" ] || INSTALL_DIR="${HOME:-}/.local/bin"
[ "$INSTALL_DIR" != "/.local/bin" ] || die "cannot determine your home directory; pass --install-dir"

# --- what machine is this -------------------------------------------------

detect_target() {
    os="$(uname -s)"
    arch="$(uname -m)"
    case "$os" in
        Linux) os_part="unknown-linux-gnu" ;;
        Darwin) os_part="apple-darwin" ;;
        *) die "unsupported operating system: $os (OXIDE ships Linux and macOS builds)" ;;
    esac
    case "$arch" in
        x86_64 | amd64) arch_part="x86_64" ;;
        aarch64 | arm64) arch_part="aarch64" ;;
        *) die "unsupported architecture: $arch (OXIDE ships x86_64 and aarch64 builds)" ;;
    esac
    # There is no Intel macOS build, and there cannot be one yet: the ONNX
    # Runtime OXIDE links publishes no x86_64-apple-darwin artifact. Say so
    # here rather than letting the download 404 on an asset that will never
    # exist. Worth checking Rosetta first — an x86_64 shell on an Apple
    # Silicon Mac reports x86_64 from `uname -m`.
    if [ "$os_part" = "apple-darwin" ] && [ "$arch_part" = "x86_64" ]; then
        die "Intel macOS is not supported: OXIDE's embedding runtime publishes no
x86_64 macOS build.

If this is an Apple Silicon Mac, you are in a Rosetta shell. Start a native
one and re-run:
  arch -arm64 /bin/sh

Otherwise, build from source:
  https://github.com/$REPO#building-from-source"
    fi
    printf '%s-%s' "$arch_part" "$os_part"
}

# --- download helpers -----------------------------------------------------

# One of curl or wget, chosen once. `file://` works with both, which is what
# lets OXIDE_BASE_URL point at a local directory.
if command -v curl >/dev/null 2>&1; then
    DOWNLOADER=curl
elif command -v wget >/dev/null 2>&1; then
    DOWNLOADER=wget
else
    die "either \`curl\` or \`wget\` is required"
fi

fetch() {
    # fetch URL DEST — non-zero (and no usable DEST) on any failure.
    case "$1" in
        # Handled here rather than by the downloader: GNU wget rejects
        # file:// outright ("Unsupported scheme"), so an offline or mirrored
        # install would work under curl and fail under wget.
        file://*)
            src="${1#file://}"
            [ -f "$src" ] || return 1
            cp "$src" "$2" || return 1
            return 0
            ;;
    esac
    case "$DOWNLOADER" in
        curl) curl -fsSL --retry 3 -o "$2" "$1" ;;
        wget) wget -q -O "$2" "$1" ;;
    esac
}

resolve_latest() {
    # GitHub redirects /releases/latest to /releases/tag/<version>; reading
    # the redirect target avoids the API, its rate limit, and needing a JSON
    # parser on the user's machine.
    url="https://github.com/$REPO/releases/latest"
    case "$DOWNLOADER" in
        curl)
            effective="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$url" 2>/dev/null || true)"
            ;;
        wget)
            # `--spider -S` prints the response headers to stderr; the last
            # Location is where the redirect chain ended up.
            effective="$(wget -q -S --spider --max-redirect=10 "$url" 2>&1 |
                sed -n 's/^[[:space:]]*Location:[[:space:]]*\([^[:space:]]*\).*$/\1/p' |
                tail -1)"
            ;;
    esac
    case "$effective" in
        */tag/*) printf '%s' "${effective##*/tag/}" ;;
        *) return 1 ;;
    esac
}

# --- checksum -------------------------------------------------------------

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d' ' -f1
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 "$1" | sed 's/^.*= *//'
    else
        return 1
    fi
}

# --- go -------------------------------------------------------------------

TARGET="$(detect_target)"

if [ -z "$VERSION" ]; then
    [ -z "$BASE_URL" ] || die "OXIDE_BASE_URL needs an explicit --version"
    VERSION="$(resolve_latest || true)"
    [ -n "$VERSION" ] || die "could not determine the latest release of $REPO; pass --version explicitly"
fi
# Accept `0.1.0` as well as `v0.1.0`; assets are named with the `v`.
case "$VERSION" in
    v*) ;;
    *) VERSION="v$VERSION" ;;
esac

[ -n "$BASE_URL" ] || BASE_URL="https://github.com/$REPO/releases/download/$VERSION"

ARCHIVE="$BIN-$VERSION-$TARGET.tar.gz"

TMPDIR_OXIDE=""
STAGING=""
cleanup() {
    [ -z "$TMPDIR_OXIDE" ] || rm -rf "$TMPDIR_OXIDE"
    [ -z "$STAGING" ] || rm -f "$STAGING"
}
# EXIT does the cleaning; the signal traps only exit, which then fires EXIT.
# They must exit rather than merely clean up: a POSIX shell resumes where it
# left off once a signal handler returns, so a handler that just tidied the
# temp directory would let a Ctrl-C land *between* staging the new binary and
# moving it into place — and the interrupted upgrade would complete anyway.
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 129' HUP
trap 'exit 143' TERM

TMPDIR_OXIDE="$(mktemp -d "${TMPDIR:-/tmp}/oxide-install.XXXXXX")" ||
    die "could not create a temporary directory"

say "Installing OXIDE $VERSION ($TARGET)"

archive_path="$TMPDIR_OXIDE/$ARCHIVE"
fetch "$BASE_URL/$ARCHIVE" "$archive_path" ||
    die "could not download $BASE_URL/$ARCHIVE
Check that $VERSION exists and publishes a $TARGET build:
  https://github.com/$REPO/releases"

sums_path="$TMPDIR_OXIDE/SHA256SUMS"
fetch "$BASE_URL/SHA256SUMS" "$sums_path" ||
    die "could not download $BASE_URL/SHA256SUMS — refusing to install an unverified binary"

expected="$(sed -n "s|^\\([0-9a-f]\\{64\\}\\)[ *]\\{1,\\}\\(\\./\\)\\{0,1\\}$ARCHIVE\$|\\1|p" "$sums_path" | head -1)"
[ -n "$expected" ] ||
    die "SHA256SUMS for $VERSION does not list $ARCHIVE — refusing to install an unverified binary"

actual="$(sha256_of "$archive_path")" ||
    die "no SHA-256 tool found (need one of sha256sum, shasum, or openssl) — refusing to install an unverified binary"

if [ "$expected" != "$actual" ]; then
    die "checksum mismatch for $ARCHIVE — refusing to install
  expected $expected
  actual   $actual"
fi
say "Checksum verified."

need tar
extract_dir="$TMPDIR_OXIDE/unpacked"
mkdir -p "$extract_dir"
tar -xzf "$archive_path" -C "$extract_dir" || die "could not unpack $ARCHIVE"

staged="$extract_dir/$BIN"
[ -f "$staged" ] || die "$ARCHIVE does not contain a \`$BIN\` binary"
chmod +x "$staged" || die "could not make the downloaded binary executable"

# Run it before it is installed: a binary that cannot start on this machine
# must not replace one that can.
staged_version="$("$staged" --version 2>/dev/null || true)"
[ -n "$staged_version" ] ||
    die "the downloaded binary did not run on this machine, so the existing installation was left alone.
This usually means the system libraries are older than the build requires."

mkdir -p "$INSTALL_DIR" || die "could not create $INSTALL_DIR"
[ -w "$INSTALL_DIR" ] ||
    die "$INSTALL_DIR is not writable by you.
Pass --install-dir with somewhere you own, e.g.
  ./install.sh --install-dir \"\$HOME/.local/bin\""

dest="$INSTALL_DIR/$BIN"
# `mv file dir` moves the file *into* the directory, so a directory sitting
# where the binary belongs would silently produce $INSTALL_DIR/oxide/oxide
# and an install that reports success while nothing is on PATH.
[ ! -d "$dest" ] || die "$dest is a directory, not a binary; remove it or pass a different --install-dir"
# Stage inside the destination directory so the final step is a same
# filesystem `mv`, which replaces the old binary in one operation. That
# matters beyond crash-safety: `oxide install` records this absolute path in
# each coding agent's MCP config, so an upgrade has to land on exactly this
# path rather than remove and recreate it.
STAGING="$INSTALL_DIR/.$BIN.new.$$"
rm -f "$STAGING"
cp "$staged" "$STAGING" || die "could not write to $INSTALL_DIR"
chmod 755 "$STAGING" || die "could not set permissions on $STAGING"
mv -f "$STAGING" "$dest" || die "could not install to $dest"
STAGING=""

say "Installed $dest"
say "$("$dest" --version)"

case ":${PATH:-}:" in
    *":$INSTALL_DIR:"*)
        say ""
        say "Next:"
        say "  oxide index"
        say "  oxide query \"Where is authentication handled?\""
        say "  oxide install     # connect OXIDE to your coding agents"
        ;;
    *)
        say ""
        say "$INSTALL_DIR is not on your PATH. Add it:"
        say ""
        say "  export PATH=\"$INSTALL_DIR:\$PATH\""
        say ""
        say "Put that line in your shell profile — ~/.bashrc, ~/.zshrc, or"
        say "the fish equivalent at ~/.config/fish/config.fish — then start a"
        say "new shell and run:"
        say "  oxide index"
        say "  oxide install     # connect OXIDE to your coding agents"
        ;;
esac
