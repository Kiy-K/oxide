# Platforms

OXIDE is one pure-Python package (`py3-none-any` wheel) on native wheels. Which
platforms it supports is set by those wheels and by the pinned FTS extension builds,
and checked by `.github/workflows/platforms.yml`.

## Support matrix

| Platform | Runner | Native wheels (CPython 3.12–3.14) | FTS 0.21.0 build | Status |
| --- | --- | --- | --- | --- |
| Linux x86_64 (glibc ≥ 2.28) | `ubuntu-24.04` | ladybug, tree-sitter, grammars | `linux_amd64` | supported; verified |
| Windows x86_64 | `windows-2025` | yes, plus OpenSSL 3 (below) | `win_amd64` | supported; verified |
| macOS 15+ Intel | `macos-15-intel` | yes (ladybug needs macOS 15) | `osx_amd64` | supported; verified |
| macOS 15+ Apple silicon | `macos-15` | yes (ladybug needs macOS 15) | `osx_arm64` | supported; verified |
| Linux arm64, musl, Windows arm64 | none | wheels exist | not pinned | unsupported: not tested; the lexical channel reports `unavailable` |
| macOS < 15 | none | no ladybug wheel | — | unsupported: does not install |

The Linux job also runs on CPython 3.12 and 3.14, the ends of `requires-python`
(rows `linux-x86_64-py3.12` and `linux-x86_64-py3.14`). Their status is pending until
those jobs have run green on the hosted runners. Locally on Linux x86_64 (2026-10-18), the wheel
installed from its own metadata, without the lock, and passed `smoke_dist.py` on
3.12.15, 3.13.16 and 3.14.8.

"Verified" means the platform job has passed. A row stays "pending" until its hosted
job has run green; nothing here is claimed from local runs on another OS.

Python is the project's `requires-python` (3.12–3.14; ladybug 0.21.0 has wheels for
3.10–3.14). The jobs use the pinned `.python-version` (3.13).

## Windows prerequisite: OpenSSL 3

ladybug's Windows wheel links `libssl-3-x64.dll` and `libcrypto-3-x64.dll` but
deliberately leaves them out (its `DELVEWHEEL` record passes `--exclude` for both, in
0.21.0, 0.21.2 and the 0.22 dev builds as of 2026-10-18). Python does not search `PATH` for an
extension's DLLs, so the OpenSSL 3 x64 DLLs must be in the Windows system directory,
for example from the Win64 OpenSSL installer with its "copy DLLs to the Windows system
directory" option. Without them every store operation fails with
`native_unavailable` before anything is touched. The Windows job installs them the
same way.

## Windows limitation: ASCII store paths

On Windows, ladybug cannot open a database or extension path with non-ASCII
characters. On the hosted runner it failed with "Cannot open file ... Error 3" for a
path containing `é` (Platforms run 39108643199). OXIDE checks this before creating
anything: a non-ASCII store path is `invalid_path`, and a non-ASCII FTS extension
path makes the lexical channel `unavailable` (and `oxide prepare` refuses it before
downloading). Each error names the variable to set. With a non-ASCII user name, set
`OXIDE_DATA_DIR` and `OXIDE_FTS_EXTENSION` to an ASCII location. Repository paths and
file names may be anything; the smoke run indexes a non-ASCII repository there.

No automatic fallback is safe, so OXIDE fails closed instead of choosing a location
(investigated 2026-10-18):

- 8.3 short names (`GetShortPathNameW`) exist only if the volume creates them. That is
  a per-volume setting, and Dev Drive/ReFS has none. They also exist only for paths
  that already exist, so the store's identity would depend on volume configuration.
- `%LOCALAPPDATA%` is under the same non-ASCII profile directory.
- A machine-wide ASCII directory (`%ProgramData%`, `C:\oxide`) is shared between users,
  so other accounts could read or plant store contents.
- The UTF-8 active code page is a process manifest or system-wide setting, not
  something a library can switch on.

The fix belongs upstream: LadybugDB opening paths with the wide-character Windows
APIs.

## Why ladybug is pinned at 0.21.0

The FTS extension must match the ladybug core it loads into. The host publishes FTS
only as `v0.21.0` (there is no 0.21.1 or 0.21.2 build, checked 2026-10-18), and its
compiled-in version table ends at 0.21.0. ladybug 0.21.2's ends at 0.21.1. Linux and
macOS ran that mismatched pair cleanly. On Windows an FTS query crashed the process
with an access violation, after a second generation was published in the same process
(Platforms run 39104738648). That is the patch-level ABI mismatch upstream has seen
before (LadybugDB/ladybug#971). So the ladybug pin is 0.21.0, whose version table
matches the extension. Both ContextBench gates gave identical entries, routes,
payloads and metrics on 0.21.0 (`baseline-v7`, `typescript-v4`). Move the pin forward
together with a matching FTS build.

## FTS extension pins

`oxide prepare` downloads the platform's build of the LadybugDB FTS extension 0.21.0
from the OXIDE release `ladybug-fts-0.21.0`
(`github.com/Kiy-K/oxide/releases/download/ladybug-fts-0.21.0/libfts-0.21.0-<platform>.lbug_extension`).
It checks the download against the SHA-256 pinned in `ladybug.FTS_BUILDS`, and the
engine checks it again before every load. The release holds LadybugDB's own builds,
unmodified, with LadybugDB's license and third-party notices
([fts-mirror/](phase-6/release-blockers/fts-mirror/RELEASE.md)). The digests were taken
on 2026-10-17 (file types: ELF x86-64, Mach-O x86_64, Mach-O arm64, PE32+ x86-64). On a
platform without a pin, `fts_unavailable` names the platform, and `oxide prepare` fails
with the same message without touching the network. Search and context still run with
the lexical channel `unavailable`. That is a supported degraded mode, not a failure.

**Provenance (verified 2026-10-24):** all four pinned files are byte-identical to the
artifacts of upstream CI run 36488319988, built at `d7225cea54`, the `v0.21.0` tag that
`ladybug==0.21.0` is built from. OXIDE mirrors them because upstream's
`extension.ladybugdb.com/v0.21.0/` is not immutable. Since 2026-10-20 it has served a
build of upstream main at `225bc28088` (core 0.22.0-dev) for Linux x86-64, macOS x86-64
and Windows. That build fails to load into core 0.21.0 (undefined symbol, measured on
Linux). To move the pin forward, mirror the new version's release builds the same way,
after checking that they load into the matching core. See
[docs/phase-6/release-blockers/](phase-6/release-blockers/README.md).

CI fetches the FTS build cold on every run, so a missing or replaced asset fails CI
instead of hiding behind a cache.

## What each platform job checks

1. `uv sync --locked`: the locked native wheels install.
2. `oxide prepare`: the platform's pinned FTS build downloads and verifies.
   `smoke_dist.py --environment` records the platform, Python, package versions and
   FTS state (`environment.json` artifact).
3. `mypy --strict`: type-checks the platform's own branches (`msvcrt` on Windows,
   `fcntl` elsewhere).
4. `pytest`, without the ContextBench gates (`test_contextbench.py`,
   `test_contextbench_ts.py`; they run on Linux in `ci.yml` and `mise run contextbench-ts`):
   - indexing, reopening and incremental parity;
   - FTS queries;
   - search, context, the CLI and MCP over stdio and HTTP;
   - generation publication and crash recovery (`test_recovery.py`,
     `test_persistence.py`);
   - the cross-process store lock and resource release (`test_lifecycle.py`);
   - paths with quotes, spaces and non-ASCII characters (`test_native.py`).
5. `uv build`, then the wheel, resolved by pip from its own metadata alone (no lock,
   pip's default settings). The job fails if pip would build any dependency from source.
   Then pip installs it wheel-only, and the `smoke_dist.py` checks below run against
   that install. A lock can hide what pip picks: pip takes the newest release even when
   it has only an sdist.
6. `scripts/smoke_dist.py` against the wheel and the sdist, each
   installed into a fresh environment with only the locked runtime dependencies:
   - the installed `oxide` console script indexes a copy of the Python and TypeScript
     fixtures plus a non-ASCII, CRLF file, from a data path with spaces and non-ASCII
     characters;
   - the persisted store reopens unchanged, and an edit publishes a new generation;
   - FTS search finds the non-ASCII declaration;
   - a `--file` hint leads the context;
   - `oxide mcp` answers over stdio;
   - a second process gets `ownership_conflict` while the first holds the store.

Checkout turns off `core.autocrlf`. Fixtures and frozen spans are byte-exact, and a
CRLF checkout would change their digests and offsets.

## Intel macOS: cryptography below 49

fastmcp needs `cryptography` (through authlib, joserfc and pyjwt), and pyca stopped
shipping Intel macOS wheels in 49.0.0. On Intel macOS, a plain `pip install` would pick
the 50.x sdist, and building it needs a Rust toolchain. So `pyproject.toml` declares two
requirements with environment markers. Both are in the wheel's `Requires-Dist`, so pip
follows them as well as uv:

- `cryptography<49` on `darwin`/`x86_64`, which resolves to 48.0.1 (universal2
  wheels);
- `cryptography>=50` everywhere else, which resolves to 50.0.2.

The lock forks on these markers. Every consumer accepts 48.x (the tightest bound is
`>=45.0.1`).

The cost is that Intel macOS runs an older `cryptography`. As of 2026-10-24, 48.0.1
carries three pyca advisories:

- GHSA-g6cj-pr64-35w5: PKCS#7 decryption (`pkcs7_decrypt_*`);
- GHSA-jwv3-5hgf-82ww and GHSA-m2h6-j472-rp4c: the X.509 path verifier
  (`cryptography.x509.verification`).

Neither OXIDE nor any installed dependency calls those APIs (searched 2026-10-24). The
wheel also bundles OpenSSL 4.0.1, against 4.0.3 in 50.0.2. The advisories fixed in
4.0.3 are mostly in TLS, DTLS and QUIC, which `cryptography` does not expose.

The floor elsewhere keeps every other platform on the patched release. Remove both
lines when Intel macOS support is dropped, or if pyca ships x86_64 wheels again.

## Platform-specific behavior

- **Store lock:** `fcntl.flock` (POSIX) or `msvcrt.locking` on one byte (Windows),
  non-blocking. Either way a held lock is `ownership_conflict` and is released when
  its descriptor closes.
- **Durability:** files and directories are fsynced before `CURRENT` moves. Windows
  cannot open a directory to flush it, so there a rename's durability rests on NTFS
  metadata journaling (a `shortcut:` comment in `_fsync`).
- **Text:** every file OXIDE writes or reads is UTF-8 or bytes, never the locale
  encoding.
- **Data and cache locations** use the XDG layout on every OS (`~/.local/share/oxide`,
  `~/.cache/oxide`), or `OXIDE_DATA_DIR` / `OXIDE_FTS_EXTENSION`. Native per-OS
  locations are a packaging decision left open.

## Running it

- **Linux:** `mise run verify`, plus the artifact smoke:
  `uv build --out-dir DIST`, a fresh `uv venv VENV`, `uv pip install --python VENV DIST/*.whl -r
  <(uv export --locked --no-dev --no-emit-project --no-hashes)`, then
  `VENV/bin/python -I scripts/smoke_dist.py fixtures`.
- **The Linux leg under act** (Linux containers only). With podman 5 and act 0.2.89
  it does not run as of 2026-10-17: checkout works, but act cannot copy any
  JavaScript action (`mise-action`, `upload-artifact`) into the container. Podman
  rejects the image's `/var/run` → `/run` symlink as "path escapes from parent".
  This is not a workflow defect. It should work with Docker. Run the working tree
  from a clean `git worktree`, because act copies the whole directory, and point
  `TMPDIR` off a small `/tmp`:

      systemctl --user start podman.socket
      DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock act push \
        -W .github/workflows/platforms.yml --matrix label:linux-x86_64 \
        -P ubuntu-24.04=ghcr.io/catthehacker/ubuntu:act-24.04 \
        --container-daemon-socket - \
        --container-options "--cpus 2 --memory 3g -v $HOME/.cache/oxide:/root/.cache/oxide" \
        --artifact-server-path /tmp/act-artifacts

- **Windows and macOS** can only run on the hosted runners. act cannot emulate them.

## JavaScript grammar compatibility

`tree-sitter-javascript==0.23.1` ships CPython `cp39-abi3` wheels covering all
four supported OS/architecture rows above, usable on Python 3.12–3.14. Its grammar
ABI is 14; the locked tree-sitter 0.26.0 accepts ABI 13–15. Published wheel metadata
and local runtime checks are recorded in
[phase-6/javascript-v1](phase-6/javascript-v1/README.md). The existing platform
workflow automatically includes the new conformance fixtures and distribution smoke.
New JavaScript execution on Windows/macOS is pending those hosted jobs; wheel
availability does not establish runtime verification. Existing platform limitations
still apply. No Node or Bun runtime dependency is introduced.

## Go grammar compatibility

`tree-sitter-go==0.25.0` ships CPython `cp310-abi3` wheels for Linux x86_64/aarch64,
macOS x86_64/arm64 and Windows amd64/arm64. Its grammar ABI is 15; the locked
tree-sitter 0.26.0 accepts ABI 13–15. Fixture conformance and Linux distribution
smoke evidence are recorded in [phase-6/go-v1](phase-6/go-v1/README.md). Runtime
verification on Windows and macOS is pending hosted CI; wheel availability alone
does not establish runtime verification. Existing platform limitations still apply.

## Rust grammar compatibility

`tree-sitter-rust==0.24.2` ships CPython `cp39-abi3` wheels for Linux x86_64/aarch64
(manylinux and musllinux), macOS x86_64/arm64 and Windows amd64/arm64. Its grammar ABI is
15; the locked tree-sitter 0.26.0 accepts ABI 13–15. Cargo layout is read with the standard
library's `tomllib`, so no Rust toolchain, `cargo` or `rust-analyzer` is needed or run
(the installed-artifact smoke runs with host toolchains off `PATH`). Fixture
conformance and Linux distribution smoke evidence are recorded in
[phase-6/rust-v1](phase-6/rust-v1/README.md). Runtime verification on Windows and macOS is
pending hosted CI; wheel availability alone does not establish runtime verification.
Existing platform limitations still apply.
