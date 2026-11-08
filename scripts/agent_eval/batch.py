"""What a results file is bound to: the code, prompt, arm config, and agent binary."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import arms
import codex
import protocol
import tasks

from oxide.storage.fts import fts_location


def runner_version(runner: str) -> str:
    binary = codex.CODEX if runner == "codex" else arms.OPENCODE
    done = subprocess.run([str(binary), "--version"], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def source_sha256() -> str:
    """Identity of the code that ran: HEAD, uncommitted edits, and untracked files under the
    engine and the harness. Any edit to them changes the manifest, so a resumed results file
    is never a mix of two code states."""

    def git(*args: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(tasks.ROOT), *args], capture_output=True, check=True
        ).stdout

    paths = ["src", "scripts/agent_eval"]
    untracked = sorted(git("ls-files", "--others", "--exclude-standard", "--", *paths).split())
    parts = [git("rev-parse", "HEAD"), git("diff", "HEAD", "--binary", "--", *paths)]
    for name in untracked:
        parts += [name, (tasks.ROOT / name.decode()).read_bytes()]
    return protocol.sha256_bytes(b"\0".join(parts))


def tree_sha256(root: Path) -> str:
    """Identity of a source tree: every Python file's relative path and bytes, in path order."""
    parts: list[bytes] = []
    for path in sorted(root.rglob("*.py")):
        parts += [str(path.relative_to(root)).encode(), path.read_bytes()]
    return protocol.sha256_bytes(b"\0".join(parts))


def manifest(args: argparse.Namespace, chosen: list[dict[str, Any]]) -> dict[str, Any]:
    """Everything a result depends on. Spend cap and parallelism are not in it: they change how
    many runs happen, not what each run is."""
    placeholder = Path("WORKTREE")
    return {
        "protocol": protocol.PROTOCOL,
        "split": args.split,
        "tasks_file_sha256": protocol.sha256_bytes(tasks.path_for(args.split).read_bytes()),
        "task_ids_sha256": protocol.sha256_bytes("\n".join(t["id"] for t in chosen).encode()),
        "tasks": len(chosen),
        "reps": args.reps,
        "model": args.model,
        "timeout": args.timeout,
        "prompt_sha256": protocol.sha256_bytes(
            f"{arms.PROMPT}\n{json.dumps(arms.HINT, sort_keys=True)}".encode()
        ),
        "arm_config_sha256": protocol.sha256_bytes(
            json.dumps(
                {
                    a: codex.codex_config(
                        arms.mcp_command(a, placeholder, placeholder, placeholder), fts_location()
                    )
                    if args.runner == "codex"
                    else arms.config(a, placeholder, placeholder, placeholder)
                    for a in arms.ARM_NAMES
                },
                sort_keys=True,
            ).encode()
        ),
        "arms": list(args.arms),
        "base_src_sha256": tree_sha256(args.base_src) if args.base_src else None,
        # OpenCode enforces DENY through its config; Codex does not read that config and runs
        # under its workspace-write sandbox, so no DENY is recorded for it.
        "deny": arms.DENY if args.runner == "opencode" else None,
        "runner": args.runner,
        "runner_version": runner_version(args.runner),
        "harness_source_sha256": source_sha256(),
    }
