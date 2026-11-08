"""Confinement of git and traces on the agent's tree, and the hidden-test score."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import arms
import confine
import tasks

# Hooks and fsmonitor off. Filters still run, so git_on_agent confines every command on this tree.
GIT_SAFE = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null")


def git_on_agent(base: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Git on the agent's tree, confined. Its clean and smudge filters must not run on the host."""
    import run

    argv = confine.confine(["git", *GIT_SAFE, *args], writable=base, hide=run.hidden(), net=False)
    return tasks.sh(*argv, cwd=cwd)


def write_private(path: Path, text: str) -> None:
    """Replace ``path`` with a new regular file. The agent can plant a symlink or hardlink
    here; following it would truncate a host file."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except IsADirectoryError:
        raise RuntimeError(f"trace path is a directory: {path}") from None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def confined(base: Path, net: bool) -> Callable[[list[str]], list[str]]:
    def wrap(argv: list[str]) -> list[str]:
        import run

        return confine.confine(argv, writable=base, hide=run.hidden(), net=net)

    return wrap


LOG_CAP = 8 << 20


def log_text(path: Path) -> str:
    """A log the agent could write, read on the host: a regular file only, bounded. A planted
    symlink, FIFO or device reads as empty instead of hanging or exhausting the harness."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return ""
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return ""
        with os.fdopen(os.dup(fd), "rb") as file:
            return file.read(LOG_CAP).decode("utf-8", errors="replace")
    finally:
        os.close(fd)


def isolation(arm: str, base: Path) -> None:
    """Reject runs that loaded MCPs other than the one explicitly configured for the arm."""
    log = "\n".join(log_text(p) for p in sorted((base / "data" / "opencode" / "log").glob("*.log")))
    seen = set(re.findall(r'message="mcp connected" server=(\S+)', log))
    expected = {"oxide"} if arm in arms.OXIDE_ARMS else set()
    if seen != expected:
        raise RuntimeError(f"isolation: unexpected MCP servers {sorted(seen)}")


def score(task: dict[str, Any], work: Path, env: dict[str, str], base: Path) -> dict[str, Any]:
    """Hidden tests on the agent's tree; its own test edits are reverted first.
    An id the run did not report is a failed test. ``tasks.TestRunError`` is infrastructure
    and propagates, so the caller records a harness error rather than an unresolved task."""
    for path in task["test_files"]:
        listed = git_on_agent(base, "cat-file", "-e", f"HEAD:{path}", cwd=work)
        if listed.returncode == 0:
            git_on_agent(base, "checkout", "HEAD", "--", path, cwd=work)
        else:
            # rm, confined: a planted symlinked parent must not redirect the delete onto the host.
            tasks.sh(*confined(base, net=False)(["rm", "-f", "--", path]), cwd=work)
    tasks.apply(work, task["test_patch"], wrap=confined(base, net=False), scratch=base)
    outcome = tasks.tests(
        work, env, task["test_files"], wrap=confined(base, net=False), agent_tree=True
    )
    failed = [t for t in task["fail_to_pass"] if outcome.get(t) != "PASSED"]
    broken = [t for t in task["pass_to_pass"] if outcome.get(t) != "PASSED"]
    return {
        "resolved": not failed and not broken,
        "f2p_failed": len(failed),
        "p2p_broken": len(broken),
    }


def connected(data: Path, arm: str) -> int:
    """How many OXIDE tools OpenCode connected in this run, from its log. A run that never calls
    the tool with this count at 1 has ignored a connected tool, which is a different finding from
    one where the tool never reached the agent."""
    log = data / "opencode" / "log" / "opencode.log"
    if arm not in arms.OXIDE_ARMS:
        return 0
    counts = [
        int(m.group(1))
        for line in log_text(log).splitlines()
        if "mcp connected" in line and "server=oxide" in line
        if (m := re.search(r"tools=(\d+)", line))
    ]
    return max(counts, default=0)
