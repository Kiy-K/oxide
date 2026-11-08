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
)


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
    """``argv`` under bubblewrap: the host read-only, ``/tmp`` and every ``hide`` path replaced by
    an empty tmpfs (a file by ``/dev/null``), ``writable`` the only writable path. ``net=False``
    also cuts the network. Mounts apply in order, so ``writable`` is bound last and stays
    visible when it lives under a hidden directory."""
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
