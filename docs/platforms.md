# Platforms

OXIDE is one pure-Python package (`py3-none-any` wheel) on native wheels. Which
platforms it supports is set by those wheels and by the pinned FTS extension builds,
and checked by `.github/workflows/platforms.yml`.

## Support matrix

| Platform | Runner | Native wheels (CPython 3.12–3.14) | FTS 0.21.0 build | Status |
| --- | --- | --- | --- | --- |
| Linux x86_64 (glibc ≥ 2.28) | `ubuntu-24.04` | ladybug, tree-sitter, grammars | `linux_amd64` | supported; verified |
| Windows x86_64 | `windows-2025` | yes | `win_amd64` | supported; hosted run pending |
| macOS 15+ Intel | `macos-15-intel` | yes (ladybug needs macOS 15) | `osx_amd64` | supported; hosted run pending |
| macOS 15+ Apple silicon | `macos-15` | yes (ladybug needs macOS 15) | `osx_arm64` | supported; hosted run pending |
| Linux arm64, musl, Windows arm64 | none | wheels exist | not pinned | unsupported: not tested; the lexical channel reports `unavailable` |
| macOS < 15 | none | no ladybug wheel | — | unsupported: does not install |

"Verified" means the platform job has passed. A row stays "pending" until its hosted
job has run green; nothing here is claimed from local runs on another OS.

Python is the project's `requires-python` (3.12–3.14; ladybug 0.21.2 has wheels for
3.10–3.14). The jobs use the pinned `.python-version` (3.13).

## FTS extension pins

`oxide prepare` downloads the platform's build of the LadybugDB FTS extension 0.21.0
from `extension.ladybugdb.com` and checks it against the SHA-256 pinned in
`ladybug.FTS_BUILDS`. The engine checks it again before every load. Each digest was
taken from one download on 2026-10-17 (file types: ELF x86-64, Mach-O x86_64, Mach-O
arm64, PE32+ x86-64). On a platform without a pin, `fts_unavailable` names the
platform, `oxide prepare` fails with the same message without touching the network,
and search and context still run with the lexical channel `unavailable`. That is a
supported degraded mode, not a failure.

The extension host is slow (measured at about 80 KB/min for one sequential download
on 2026-10-17; parallel downloads were fast), so the workflow caches
`~/.cache/oxide` per platform.

## What each platform job checks

1. `uv sync --locked`: the locked native wheels install.
2. `oxide prepare`: the platform's pinned FTS build downloads and verifies.
   `smoke_dist.py --environment` records the platform, Python, package versions and
   FTS state (`environment.json` artifact).
3. `mypy --strict`: type-checks the platform's own branches (`msvcrt` on Windows,
   `fcntl` elsewhere).
4. `pytest`, without `test_contextbench.py` (its corpora gate runs on Linux in
   `ci.yml`):
   - indexing, reopening and incremental parity;
   - FTS queries;
   - search, context, the CLI and MCP over stdio and HTTP;
   - generation publication and crash recovery (`test_recovery.py`,
     `test_persistence.py`);
   - the cross-process store lock and resource release (`test_lifecycle.py`);
   - paths with quotes, spaces and non-ASCII characters (`test_native.py`).
5. `uv build`, then `scripts/smoke_dist.py` against the wheel and the sdist, each
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
  `uv build --out-dir DIST`, a fresh `uv venv`, `uv pip install DIST/*.whl -r
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
