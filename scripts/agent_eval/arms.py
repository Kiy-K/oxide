"""Arm names, the shared prompt, and the OXIDE command each arm runs."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

OPENCODE = Path(shutil.which("opencode") or "opencode").resolve()
RUNNERS = ("opencode", "codex")

ARMS = ("control", "oxide")
# oxide-base: the oxide arm with the tool description of a --base-src snapshot (an A/B control).
ARM_NAMES = (*ARMS, "oxide-base")
OXIDE_ARMS = ("oxide", "oxide-base")
PROMPT = """You are in a Python repository at the current directory. Resolve the issue below by \
editing the source code in place. Do not edit or add tests: the maintainers' tests are hidden. \
A virtual environment with the project installed (editable) and pytest is active, so you can \
run code and the existing tests. Stop when the fix is complete.{hint}

<issue>
{problem}
</issue>"""
OXIDE_HINT = (
    "\nAn OXIDE code-search tool is available: mode `search` lists the code locations "
    "for a query, mode `context` returns their source."
)
HINT = {"control": "", "oxide": OXIDE_HINT, "oxide-base": OXIDE_HINT}
# Equal in both arms: no web (the fixes are public), no skills, no questions to a human.
DENY = {"webfetch": "deny", "websearch": "deny", "skill": "deny", "question": "deny"}

BOOT = (
    "import runpy, sys; sys.path.insert(0, sys.argv[1]); "
    "sys.argv = ['oxide', *sys.argv[2:]]; runpy.run_module('oxide', run_name='__main__')"
)


def base_import_root(base_src: Path) -> Path:
    """``sys.path`` entry that imports ``--base-src``. The flag is a snapshot of ``src/oxide``
    (the package directory) or of a tree that contains that package. Pure path logic, so the
    manifest can build commands from a placeholder; ``check_base_src`` validates the real one."""
    if (base_src / "oxide" / "__init__.py").is_file() or base_src.name != "oxide":
        return base_src
    return base_src.parent


def check_base_src(base_src: Path) -> None:
    if not (base_import_root(base_src) / "oxide" / "__init__.py").is_file():
        raise SystemExit(f"--base-src must be a snapshot of src/oxide: {base_src}")


def oxide_command(arm: str, base_src: Path | None, *args: str | Path) -> list[str]:
    """The ``oxide`` CLI as argv. The base arm imports the snapshot in ``base_src`` instead of
    the checkout's package, so the two OXIDE arms differ only in that source."""
    if arm == "oxide-base":
        if base_src is None:
            raise RuntimeError("oxide-base needs --base-src")
        return [sys.executable, "-c", BOOT, str(base_import_root(base_src)), *map(str, args)]
    return [sys.executable, "-m", "oxide", *map(str, args)]


def mcp_command(arm: str, work: Path, data: Path, base_src: Path | None = None) -> list[str]:
    """The argv of this arm's OXIDE MCP server; no server for control."""
    if arm not in OXIDE_ARMS:
        return []
    return oxide_command(arm, base_src, "mcp", "--repo", work, "--data-dir", data)


def config(arm: str, work: Path, data: Path, base_src: Path | None = None) -> dict[str, Any]:
    cfg: dict[str, Any] = {"autoupdate": False, "permission": DENY}
    if command := mcp_command(arm, work, data, base_src):
        # codemode off: a direct tool call, not a script run through `execute`.
        server = {"type": "local", "command": command, "enabled": True, "codemode": False}
        cfg["mcp"] = {"oxide": server}
    return cfg
