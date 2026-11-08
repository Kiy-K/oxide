"""One agent run: a clean checkout, the arm's tools, the agent, then the hidden tests.

The task's editable project and tests are present; agent edits are discarded before hidden tests.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import confine
import protocol
import tasks

from oxide.storage.fts import fts_location

OPENCODE = Path(shutil.which("opencode") or "opencode").resolve()
USER_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
# Codex runs use the ChatGPT login in CODEX_HOME/auth.json, copied into each run's isolated home.
CODEX = Path(shutil.which("codex") or "codex")
CODEX_AUTH = Path.home() / ".codex" / "auth.json"
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


def oxide_command(arm: str, base_src: Path | None, *args: str | Path) -> list[str]:
    """The ``oxide`` CLI as argv. The base arm imports the snapshot in ``base_src`` instead of
    the checkout's package, so the two OXIDE arms differ only in that source."""
    if arm == "oxide-base":
        if base_src is None:
            raise RuntimeError("oxide-base needs --base-src")
        return [sys.executable, "-c", BOOT, str(base_src), *map(str, args)]
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


def codex_config(command: list[str], fts: Path) -> str:
    """Codex config for one arm: the OXIDE server alone, or no server for control. JSON strings
    are valid TOML basic strings for these paths, so the arrays are written as JSON."""
    if not command:
        return ""
    return (
        "[mcp_servers.oxide]\n"
        f"command = {json.dumps(command[0])}\n"
        f"args = {json.dumps(command[1:])}\n"
        f"env = {{ OXIDE_FTS_EXTENSION = {json.dumps(str(fts))} }}\n"
    )


def _jsonl(events: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in events.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def measure(events: str) -> dict[str, Any]:
    """Tokens, steps and tool calls from opencode's JSON event stream."""
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    calls: dict[str, int] = {}
    steps = 0
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        part = event.get("part", {})
        if event.get("type") == "tool_use":
            calls[part.get("tool", "?")] = calls.get(part.get("tool", "?"), 0) + 1
        elif event.get("type") == "step_finish":
            steps += 1
            used = part.get("tokens", {})
            tokens["input"] += used.get("input", 0)
            tokens["output"] += used.get("output", 0) + used.get("reasoning", 0)
            tokens["cache_read"] += used.get("cache", {}).get("read", 0)
            tokens["cache_write"] += used.get("cache", {}).get("write", 0)
    return {"tokens": tokens, "steps": steps, "calls": calls}


def agent_error(events: str) -> str | None:
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "error":
            error = event.get("error", {})
            return f"{error.get('type', 'agent error')}: {error.get('message', '')}"
    return None


def agent_failure(
    status: str, events: str, error: Callable[[str], str | None] = agent_error
) -> str | None:
    if message := error(events):
        return message
    return f"agent exited with {status}" if status not in {"ok", "timeout"} else None


def git_common_dir(root: Path) -> Path:
    """The object store every checkout of this repository shares. A linked worktree's ``.git`` is
    only a pointer to it, so hiding the checkout's own ``.git`` leaves the history readable."""
    done = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(done.stdout.strip())


def checkouts(root: Path) -> list[Path]:
    """Every checkout of this repository. Each one holds its own copy of the task files."""
    done = subprocess.run(
        ["git", "-C", str(root), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        Path(line.removeprefix("worktree "))
        for line in done.stdout.splitlines()
        if line.startswith("worktree ")
    ]


def hidden(root: Path = tasks.ROOT) -> list[Path]:
    """Paths no confined process may read: every run's trace and mirror (their patches and the
    upstream fix commits), the answers in each checkout's task files, the repository's git history
    (earlier answers and held-out results are in it), and the host's credential stores."""
    trees = [root, *checkouts(root)]
    return [
        tasks.CACHE,
        tasks.MIRRORS,
        git_common_dir(root),
        *(tree / "fixtures" / "eval" for tree in trees),
        *confine.secret_hides(),
    ]


def confined(base: Path, net: bool) -> Callable[[list[str]], list[str]]:
    return lambda argv: confine.confine(argv, writable=base, hide=hidden(), net=net)


def credential_values(db: Path) -> list[str]:
    """The provider key in a run's kept OpenCode copy, so its events can be redacted."""
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT * FROM credential").fetchall()
    return [s for row in rows for value in row for s in confine.secret_values(value)]


def forget_credentials(db: Path) -> None:
    """Traces are kept, so the run's copy of the provider key is removed once the agent ends.
    VACUUM rewrites the file: a DELETE alone leaves the key bytes in free pages."""
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        conn.execute("DELETE FROM credential")
        conn.execute("VACUUM")
    finally:
        conn.close()


# Git on an agent's tree: its hooks and fsmonitor program are the agent's, so they stay off.
GIT_SAFE = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null")


def credentials(model: str, dest: Path) -> None:
    """Copy only the selected provider credential into isolated OpenCode data for this agent run."""
    db = dest / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(f"file:{USER_DB}?mode=ro", uri=True) as user:
        user.execute("VACUUM INTO ?", (str(db),))
    copy = sqlite3.connect(db, isolation_level=None)
    tables = copy.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    for (table,) in tables:
        if table == "credential":
            copy.execute("DELETE FROM credential WHERE integration_id != ?", (model.split("/")[0],))
        elif table != "migration" and not table.startswith("sqlite_"):
            copy.execute(f'DELETE FROM "{table}"')
    if not copy.execute("SELECT count(*) FROM credential").fetchone()[0]:
        raise SystemExit(f"no stored opencode credential for {model.split('/')[0]}")
    copy.execute("VACUUM")
    copy.close()


def agent(
    model: str,
    arm: str,
    task: dict[str, Any],
    base: Path,
    env: dict[str, str],
    timeout: int,
) -> tuple[str, str]:
    """Runs opencode with isolated config/data and the selected provider credential."""
    work, home = base / "work", base / "config"
    prompt = PROMPT.format(hint=HINT[arm], problem=task["problem"].strip())
    secrets = credential_values(base / "data" / "opencode" / "opencode.db")
    environment = confine.clean_env(
        {
            **env,
            "HOME": str(base / "home"),
            "PWD": str(work),
            "XDG_CONFIG_HOME": str(home),
            "XDG_DATA_HOME": str(base / "data"),
            "XDG_CACHE_HOME": str(base / "home" / ".cache"),
            "XDG_STATE_HOME": str(base / "home" / ".local" / "state"),
            "OXIDE_FTS_EXTENSION": str(fts_location()),
        }
    )
    command = [
        str(OPENCODE),
        "run",
        "--standalone",
        "--auto",
        "--format",
        "json",
        "-m",
        model,
        prompt,
    ]
    wrapped = confine.confine(command, writable=base, hide=hidden(), net=True)
    try:
        status, events = launch(wrapped, work, environment, timeout)
    finally:
        forget_credentials(base / "data" / "opencode" / "opencode.db")
    return status, confine.redact(events, secrets)


def launch(
    command: list[str], cwd: Path, environment: dict[str, str], timeout: int
) -> tuple[str, str]:
    """Runs one agent CLI to completion or timeout; a timeout kills its whole process group."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        out, _ = process.communicate(timeout=timeout)
        return ("ok" if process.returncode == 0 else f"exit {process.returncode}"), out
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        out, _ = process.communicate()
        return "timeout", out or ""


def codex_model(model: str) -> str:
    """Codex takes the bare model id under a ChatGPT login; only the openai provider is routed
    through it, so any other provider is refused rather than silently remapped."""
    provider, _, name = model.partition("/")
    if provider != "openai" or not name:
        raise SystemExit(f"--runner codex needs an openai/<model> id, got {model!r}")
    return name


def codex_agent(
    model: str,
    arm: str,
    task: dict[str, Any],
    base: Path,
    env: dict[str, str],
    timeout: int,
    command: list[str],
) -> tuple[str, str]:
    """Runs ``codex exec`` with an isolated home: the ChatGPT login and this arm's MCP server only.
    User config, user MCP servers and user instructions are not read."""
    work, home = base / "work", base / "codex"
    prompt = PROMPT.format(hint=HINT[arm], problem=task["problem"].strip())
    home.mkdir(parents=True)
    (home / "config.toml").write_text(codex_config(command, fts_location()), encoding="utf-8")
    if not CODEX_AUTH.exists():
        raise SystemExit(f"no codex login at {CODEX_AUTH}: run `codex login`")
    shutil.copy2(CODEX_AUTH, home / "auth.json")
    (home / "auth.json").chmod(0o600)
    secrets = confine.json_secrets(CODEX_AUTH)  # the login's values, redacted from kept events
    environment = confine.clean_env(
        {
            **env,
            "CODEX_HOME": str(home),
            "HOME": str(base / "home"),
            "OXIDE_FTS_EXTENSION": str(fts_location()),
        }
    )
    argv = [
        str(CODEX),
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "-m",
        codex_model(model),
        "-s",
        "workspace-write",
        "-C",
        str(work),
        prompt,
    ]
    try:
        wrapped = confine.confine(argv, writable=base, hide=hidden(), net=True)
        status, events = launch(wrapped, work, environment, timeout)
    finally:
        (home / "auth.json").unlink()  # the login is never kept: traces are retained
    return status, confine.redact(events, secrets)


def codex_measure(events: str) -> dict[str, Any]:
    """Tokens, steps and tool calls from ``codex exec --json``. Codex counts cached input inside
    input_tokens, so the uncached share is recorded as input and cache_read is not billed twice.
    Output tokens already include reasoning."""
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    calls: dict[str, int] = {}
    steps = 0
    for event in _jsonl(events):
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            kind = item.get("type")
            if kind in {"agent_message", "reasoning", "error"}:
                continue
            name = item.get("tool") if kind == "mcp_tool_call" else kind
            calls[str(name)] = calls.get(str(name), 0) + 1
        elif event.get("type") == "turn.completed":
            steps += 1
            used = event.get("usage", {})
            cached = used.get("cached_input_tokens", 0)
            tokens["input"] += used.get("input_tokens", 0) - cached
            tokens["cache_read"] += cached
            tokens["output"] += used.get("output_tokens", 0)
    return {"tokens": tokens, "steps": steps, "calls": calls}


def codex_agent_error(events: str) -> str | None:
    """A failed turn, or an error with no completed turn. An error inside a turn that then
    completed is a retry, not a failure."""
    stream = _jsonl(events)
    failed = [
        e.get("error", {}).get("message", "turn failed")
        for e in stream
        if e.get("type") == "turn.failed"
    ]
    if failed:
        return str(failed[-1])
    if any(e.get("type") == "turn.completed" for e in stream):
        return None
    errors = [str(e.get("message", "agent error")) for e in stream if e.get("type") == "error"]
    return errors[-1] if errors else None


def codex_isolation(arm: str, events: str) -> None:
    """Reject runs whose MCP tool calls came from a server other than the arm's own."""
    servers = {
        str(e["item"].get("server"))
        for e in _jsonl(events)
        if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "mcp_tool_call"
    }
    if not servers <= ({"oxide"} if arm in OXIDE_ARMS else set()):
        raise RuntimeError(f"isolation: unexpected MCP servers {sorted(servers)}")


def codex_connected(arm: str) -> int:
    """OXIDE tools configured for this arm. Codex's JSON stream does not log MCP startup, so this
    is the configured count, not an observed one; the calls themselves are observed in events."""
    return 1 if arm in OXIDE_ARMS else 0


def isolation(arm: str, base: Path) -> None:
    """Reject runs that loaded MCPs other than the one explicitly configured for the arm."""
    log = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in (base / "data" / "opencode" / "log").glob("*.log")
    )
    connected = set(re.findall(r'message="mcp connected" server=(\S+)', log))
    expected = {"oxide"} if arm in OXIDE_ARMS else set()
    if connected != expected:
        raise RuntimeError(f"isolation: unexpected MCP servers {sorted(connected)}")


def score(task: dict[str, Any], work: Path, env: dict[str, str], base: Path) -> dict[str, Any]:
    """Hidden tests on the agent's tree: its own test edits are reverted first. The tree is the
    agent's, so git runs with its hooks and fsmonitor off, and pytest runs confined."""
    for path in task["test_files"]:
        listed = tasks.sh("git", *GIT_SAFE, "cat-file", "-e", f"HEAD:{path}", cwd=work)
        if listed.returncode == 0:
            tasks.sh("git", *GIT_SAFE, "checkout", "HEAD", "--", path, cwd=work)
        else:
            (work / path).unlink(missing_ok=True)
    tasks.apply(work, task["test_patch"])
    outcome = tasks.tests(work, env, task["test_files"], wrap=confined(base, net=False))
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
    if arm not in OXIDE_ARMS or not log.exists():
        return 0
    counts = [
        int(m.group(1))
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
        if "mcp connected" in line and "server=oxide" in line
        if (m := re.search(r"tools=(\d+)", line))
    ]
    return max(counts, default=0)


def run_one(
    task: dict[str, Any], arm: str, rep: int, args: argparse.Namespace, root: Path
) -> dict[str, Any]:
    key = f"{task['id']}.{arm}.{rep}"
    base = root / key
    work, data = base / "work", base / "oxide-data"
    codex = args.runner == "codex"
    row: dict[str, Any] = {
        "task": task["id"],
        "arm": arm,
        "rep": rep,
        "model": args.model,
        "split": args.split,
        "runner": args.runner,
    }
    try:
        tasks.checkout(task, work)
        env = tasks.environment(task, work, base / "venv")
        if not codex:
            home = base / "config" / "opencode"
            home.mkdir(parents=True)
            cfg = config(arm, work, data, args.base_src)
            (home / "opencode.jsonc").write_text(json.dumps(cfg), encoding="utf-8")
        if arm in OXIDE_ARMS:  # indexed before the clock starts; its cost is reported separately
            started = time.monotonic()
            indexed = tasks.sh(
                *oxide_command(arm, args.base_src, "index", work, "--data-dir", data),
                env={"OXIDE_DATA_DIR": str(data)},
            )
            row["index_seconds"] = round(time.monotonic() - started, 2)
            if indexed.returncode:
                raise RuntimeError(f"oxide index: {indexed.stderr[-300:]}")
        if codex:
            started = time.monotonic()
            command = mcp_command(arm, work, data, args.base_src)
            row["status"], events = codex_agent(
                args.model, arm, task, base, env, args.timeout, command
            )
        else:
            credentials(args.model, base / "data")
            started = time.monotonic()
            row["status"], events = agent(args.model, arm, task, base, env, args.timeout)
        row["seconds"] = round(time.monotonic() - started, 1)
        (base / "events.jsonl").write_text(events, encoding="utf-8")
        row["events_sha256"] = protocol.sha256_bytes(events.encode())
        # before any failure below: a failed run still spent tokens
        row |= (codex_measure if codex else measure)(events)
        row["spend"] = protocol.spend(row["tokens"])
        row["oxide_tools_connected"] = (
            codex_connected(arm) if codex else connected(base / "data", arm)
        )
        reason = codex_agent_error if codex else agent_error
        if failure := agent_failure(row["status"], events, reason):
            raise RuntimeError(failure)
        if codex:
            codex_isolation(arm, events)
        else:
            isolation(arm, base)
        tasks.sh("git", *GIT_SAFE, "add", "-A", cwd=work)
        diff = tasks.sh(
            "git",
            *GIT_SAFE,
            "diff",
            "--cached",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
            ".",
            ":!*.egg-info",
            cwd=work,
        )
        (base / "patch.diff").write_text(diff.stdout, encoding="utf-8")
        row |= score(task, work, env, base)
    except (RuntimeError, subprocess.SubprocessError, OSError, sqlite3.Error) as error:
        row |= {"status": "harness-error", "error": str(error)[-400:], "resolved": None}
    row["trace"] = str(base.relative_to(tasks.CACHE))  # events, patch, data: kept, never pruned
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(base / "venv", ignore_errors=True)
    return row


def runner_version(runner: str) -> str:
    binary = CODEX if runner == "codex" else OPENCODE
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
            f"{PROMPT}\n{json.dumps(HINT, sort_keys=True)}".encode()
        ),
        "arm_config_sha256": protocol.sha256_bytes(
            json.dumps(
                {
                    a: codex_config(
                        mcp_command(a, placeholder, placeholder, placeholder), fts_location()
                    )
                    if args.runner == "codex"
                    else config(a, placeholder, placeholder, placeholder)
                    for a in ARM_NAMES
                },
                sort_keys=True,
            ).encode()
        ),
        "arms": list(args.arms),
        "base_src_sha256": tree_sha256(args.base_src) if args.base_src else None,
        # OpenCode enforces DENY through its config; Codex does not read that config and runs
        # under its workspace-write sandbox, so no DENY is recorded for it.
        "deny": DENY if args.runner == "opencode" else None,
        "runner": args.runner,
        "runner_version": runner_version(args.runner),
        "harness_source_sha256": source_sha256(),
    }


def run_all(args: argparse.Namespace) -> None:
    confine.require()
    if "oxide-base" in args.arms and args.base_src is None:
        raise SystemExit("oxide-base needs --base-src: the snapshot whose tool description it runs")
    pool = sorted(tasks.load(args.split), key=tasks.order)
    chosen = [t for t in pool if not args.only or t["id"] in args.only][: args.tasks]
    root = tasks.CACHE / "runs" / time.strftime("%Y%m%dT%H%M%S")
    out = args.out or tasks.CACHE / "results" / f"{root.name}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    protocol.check_manifest(out.with_name(out.stem + ".manifest.json"), manifest(args, chosen))
    if args.split == "heldout":
        if not args.final:
            raise SystemExit("heldout is one final look: pass --final to open it")
        digest = protocol.sha256_bytes(tasks.path_for("heldout").read_bytes())
        protocol.heldout_open(tasks.CACHE / "heldout-log.jsonl", digest, out)
    existing = []
    if out.exists():  # resume: a run already recorded is not repeated, and its spend counts
        existing = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    done = {
        (r["task"], r["arm"], r["rep"])
        for r in existing
        if r.get("status") in {"ok", "timeout"} and r.get("resolved") is not None
    }
    jobs = [
        (t, a, r)
        for r in range(args.reps)
        for t in chosen
        for a in args.arms
        if (t["id"], a, r) not in done
    ]
    random.Random(0).shuffle(jobs)  # arms and tasks interleave: drift hits both alike
    ready = threading.Condition()  # guards the spend state; wakes waiting jobs as spend lands
    cap = args.max_tokens
    spend_seen = [protocol.row_spend(r) for r in existing if protocol.row_has_spend(r)]
    largest: int | None = max(spend_seen, default=None)
    state = {"spent": protocol.spent(existing), "skipped": 0, "in_flight": 0}

    def work(job: tuple[dict[str, Any], str, int]) -> None:
        nonlocal largest
        with ready:  # a run starts only if the cap still holds with every run in flight
            while not protocol.admit(state["spent"], state["in_flight"], largest, cap):
                if state["in_flight"] == 0:  # nothing running can free budget: the cap is spent
                    state["skipped"] += 1
                    return
                ready.wait()  # an in-flight run reports its spend; then the check repeats
            state["in_flight"] += 1
        try:
            row = run_one(*job, args, root)
        except BaseException:
            with ready:
                state["in_flight"] -= 1
                ready.notify_all()
            raise
        with ready, out.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row) + "\n")
            state["in_flight"] -= 1  # same lock as the spend: the cap is never blind
            if "spend" in row:
                state["spent"] += row["spend"]
                largest = max(largest or 0, row["spend"])
            ready.notify_all()
        print(
            f"{row['task']} {row['arm']}#{row['rep']}: {row['status']} resolved={row['resolved']}"
            f" spend={row.get('spend', 0)}",
            flush=True,
        )

    with ThreadPoolExecutor(args.jobs) as executor:
        list(executor.map(work, jobs))
    print(f"{len(jobs) - state['skipped']} of {len(jobs)} runs -> {out}; {state['spent']} tokens")
    if state["skipped"]:
        print(f"token cap {args.max_tokens} reached: {state['skipped']} runs not started")
