"""``oxide install``: register ``oxide mcp`` with MCP clients for the current user.

One user-wide entry per client serves every repository: each tool call names its
repository, and the server reads only stores that ``oxide index`` built. The entry runs
the interpreter that installed OXIDE, so it works from any directory. Only the entry named
``oxide`` is written; the rest of each client's configuration is left as it is.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

CLIENTS = ("claude", "codex", "cursor")
ENTRY = "oxide"
_TIMEOUT = 120  # seconds per client command; `claude mcp get` health-checks the server


class InstallError(Exception):
    """A client refused the change, or its configuration cannot be read safely."""


def server_command() -> list[str]:
    return [sys.executable, "-m", "oxide", "mcp"]


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstallError(f"{argv[0]}: {error}") from error


def _checked(argv: list[str]) -> str:
    done = _run(argv)
    if done.returncode != 0:
        raise InstallError(f"{' '.join(argv[1:3])} failed: {(done.stderr or done.stdout).strip()}")
    return done.stdout


def _executable(name: str) -> str:
    if (found := shutil.which(name)) is None:
        raise InstallError(f"{name} is not on PATH")
    return found


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise InstallError(f"{path} is not valid JSON; fix or move it and retry") from error
    if not isinstance(data, dict):
        raise InstallError(f"{path} is not a JSON object")
    return data


def _claude(command: list[str]) -> str:
    exe = _executable("claude")
    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home())
    # User-scope entries live in this file; `claude mcp get` reports only the effective one.
    servers = _read_json(config_dir / ".claude.json").get("mcpServers", {})
    entry = servers.get(ENTRY) if isinstance(servers, dict) else None
    current = entry if isinstance(entry, dict) else {}
    if current.get("command") == command[0] and current.get("args") == command[1:]:
        state = "unchanged"
    else:
        if entry is not None:
            _checked([exe, "mcp", "remove", ENTRY, "--scope", "user"])
        _checked([exe, "mcp", "add", "--scope", "user", ENTRY, "--", *command])
        state = "updated" if entry is not None else "added"
    # A local or project entry for oxide in this directory shadows the user entry.
    shown = _checked([exe, "mcp", "get", ENTRY])
    found = re.search(r"^\s*Scope: (.+)$", shown, re.MULTILINE)
    scope = found[1] if found else ""
    if scope and not scope.startswith("User"):
        return (
            f"{state} (this directory's {scope.split(' (')[0].lower()} entry overrides it; "
            "inspect and update or remove the overriding entry)"
        )
    return state


def _codex(command: list[str]) -> str:
    exe = _executable("codex")
    current = _run([exe, "mcp", "get", ENTRY, "--json"])  # nonzero exit: no such entry
    if current.returncode == 0:
        transport = json.loads(current.stdout).get("transport", {})
        if (
            transport.get("type") == "stdio"
            and transport.get("command") == command[0]
            and transport.get("args") == command[1:]
        ):
            return "unchanged"
        _checked([exe, "mcp", "remove", ENTRY])
    _checked([exe, "mcp", "add", ENTRY, "--", *command])
    return "updated" if current.returncode == 0 else "added"


def _cursor(command: list[str]) -> str:
    path = Path.home() / ".cursor" / "mcp.json"
    data = _read_json(path)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise InstallError(f"{path}: mcpServers is not a JSON object")
    entry = {"type": "stdio", "command": command[0], "args": command[1:]}
    if servers.get(ENTRY) == entry:
        return "unchanged"
    state = "updated" if ENTRY in servers else "added"
    servers[ENTRY] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".oxide-tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)  # never leave a half-written config behind
    return state


_CONFIGURE: dict[str, Callable[[list[str]], str]] = {
    "claude": _claude,
    "codex": _codex,
    "cursor": _cursor,
}


def _detected() -> list[str]:
    found = [name for name in ("claude", "codex") if shutil.which(name)]
    return [*found, *(["cursor"] if (Path.home() / ".cursor").is_dir() else [])]


def install(clients: Sequence[str] = ()) -> int:
    """Configure each client (all detected ones by default); 0 when every one succeeded."""
    chosen = list(dict.fromkeys(clients)) or _detected()
    if not chosen:
        print(
            "oxide: no MCP client found (claude, codex, or ~/.cursor); pass --client",
            file=sys.stderr,
        )
        return 1
    failed = False
    for client in chosen:
        try:
            print(f"oxide: {client}: {_CONFIGURE[client](server_command())}")
        except InstallError as error:
            failed = True
            print(f"oxide: {client}: {error}", file=sys.stderr)
    if not failed:
        print("oxide: restart the client to load the server")
    return 1 if failed else 0
