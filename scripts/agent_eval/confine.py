"""Confinement for everything that runs on an agent's tree: the agent itself, and the hidden-test
pytest that runs on its output afterwards.

The agent edits files the harness later executes (``conftest.py``, test files, ``.git/config``),
so those processes are as untrusted as the agent. Each runs under bubblewrap: the host read-only,
the answers and other runs hidden, and one writable directory. Confinement is fail-closed: with
no bubblewrap, no run starts.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

# The only host variables a confined process inherits. Credentials are not among them: the
# agent's own login is a copy inside its run directory, and every other key stays on the host.
KEEP_ENV = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "UV_CACHE_DIR",
    "UV_PYTHON_INSTALL_DIR",
)

# Home-relative credential stores, hidden whole from every confined process.
SECRET_PATHS = (
    ".codex",
    ".claude",
    ".omp",
    ".pi",
    ".ssh",
    ".gnupg",
    ".aws",
    ".config",
    ".netrc",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
    ".local/share/opencode",
    ".claude.json",
    ".cursor",
    ".copilot",
    ".gemini",
    ".cline",
    ".docker",
    ".kube",
    ".modal.toml",
    ".bash_history",
    ".zsh_history",
)

# A read-only bind still allows connect() to Unix sockets. These dirs hold the host's.
_RUNTIME = (Path("/run"), Path("/var/run"))
# libc reads these; they are often symlinks into the runtime dirs above.
_NAME_SERVICE = (Path("/etc/resolv.conf"), Path("/etc/hosts"))
_MASK_FAILED = (
    "bubblewrap confinement cannot mask host runtime sockets without breaking name resolution"
)


def runtime_dirs(candidates: Iterable[Path] = _RUNTIME) -> list[Path]:
    """Real runtime directories to replace. A symlink is not a mount point: bubblewrap
    refuses ``--tmpfs`` on one, and the directory it points at is already covered."""
    chosen: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        try:
            if not path.exists():
                continue
            real = path.resolve()
        except OSError as exc:
            raise SystemExit(_MASK_FAILED) from exc
        if real in seen or not real.is_dir():
            continue
        seen.add(real)
        chosen.append(real if path.is_symlink() else path)
    return chosen


def socket_mounts(masks: Iterable[Path], keep: Iterable[Path]) -> list[str]:
    """Replace ``masks``, then bind back each name-service file that lived under one.
    A directory or socket is not bound back: that would reopen the host sockets."""
    roots = list(masks)
    if not roots:
        return []
    args = [part for path in roots for part in ("--tmpfs", str(path))]
    made = set(roots)
    bound: set[Path] = set()
    for link in keep:
        args += _rebind(link, roots, made, bound)
    return args


def _under(path: Path, masks: list[Path]) -> bool:
    return any(path == mask or mask in path.parents for mask in masks)


def _covered_link(link: Path, masks: list[Path]) -> bool:
    """True when ``link`` or the path it names sits under a masked runtime dir."""
    if _under(link, masks):
        return True
    if not link.is_symlink():
        return False
    raw = Path(os.readlink(link))
    if not raw.is_absolute():
        raw = link.parent / raw
    return _under(Path(os.path.normpath(raw)), masks)


def _rebind(link: Path, masks: list[Path], made: set[Path], bound: set[Path]) -> list[str]:
    try:
        linked = link.is_symlink()
        present = link.exists()
    except OSError as exc:
        raise SystemExit(_MASK_FAILED) from exc
    if not present:
        if linked and _covered_link(link, masks):
            raise SystemExit(_MASK_FAILED)
        return []
    try:
        target = link.resolve(strict=True)
    except OSError as exc:
        if _covered_link(link, masks):
            raise SystemExit(_MASK_FAILED) from exc
        return []
    if not _under(target, masks) or target in bound:
        return []
    if not target.is_file():
        raise SystemExit(_MASK_FAILED)
    bound.add(target)
    args: list[str] = []
    missing: list[Path] = []
    cursor = target.parent
    while cursor not in made and cursor != Path("/"):
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        args += ["--dir", str(directory)]
        made.add(directory)
    args += ["--ro-bind", str(target), str(target)]
    return args


def require() -> None:
    """Fail closed before any run starts: without bubblewrap nothing is confined."""
    if shutil.which("bwrap") is None:
        raise SystemExit("bubblewrap (bwrap) is required: agent runs are confined, not trusted")


def clean_env(extra: Mapping[str, str]) -> dict[str, str]:
    """The allowlisted host variables, then ``extra``."""
    return {k: v for k in KEEP_ENV if (v := os.environ.get(k))} | dict(extra)


def secret_hides() -> list[Path]:
    return [p for name in SECRET_PATHS if (p := Path.home() / name).exists()]


def confine(
    argv: Iterable[str | Path], *, writable: Path, hide: Iterable[Path], net: bool
) -> list[str]:
    """``argv`` under bubblewrap: the host read-only, ``/run`` and ``/var/run`` replaced (a
    read-only bind still allows ``connect()`` to their sockets), ``/tmp`` and every ``hide``
    path replaced by an empty tmpfs (a file by ``/dev/null``), ``writable`` the only writable
    path. Name-service files that lived under those dirs are bound back, or the run is refused.
    ``net=False`` also cuts the network. Mounts apply in order, so ``writable`` is bound last
    and stays visible when it lives under a hidden directory."""
    require()
    command = [
        "bwrap",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        *socket_mounts(runtime_dirs(), _NAME_SERVICE),
    ]
    for path in (p for p in hide if p.exists()):
        if path.is_file():
            command += ["--ro-bind", "/dev/null", str(path)]
        else:
            command += ["--tmpfs", str(path)]
    command += ["--bind", str(writable), str(writable), "--die-with-parent", "--unshare-pid"]
    if not net:
        command.append("--unshare-net")
    return [*command, *map(str, argv)]


def secret_values(node: Any) -> list[str]:
    """Every string at least 12 characters long in a parsed credential document."""
    if isinstance(node, str):
        return [node] if len(node) >= 12 else []
    if isinstance(node, dict):
        return [s for value in node.values() for s in secret_values(value)]
    if isinstance(node, list):
        return [s for value in node for s in secret_values(value)]
    return []


def redact(text: str, secrets: Iterable[str]) -> str:
    """``text`` with each secret replaced. Traces are kept, so a credential a run printed must not
    reach them."""
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text


def json_secrets(path: Path) -> list[str]:
    return secret_values(json.loads(path.read_text(encoding="utf-8")))
