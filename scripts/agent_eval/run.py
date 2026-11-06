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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import protocol
import tasks

from oxide.storage.fts import fts_location

OPENCODE = Path(shutil.which("opencode") or "opencode").resolve()
USER_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"

ARMS = ("control", "oxide")
PROMPT = """You are in a Python repository at the current directory. Resolve the issue below by \
editing the source code in place. Do not edit or add tests: the maintainers' tests are hidden. \
A virtual environment with the project installed (editable) and pytest is active, so you can \
run code and the existing tests. Stop when the fix is complete.{hint}

<issue>
{problem}
</issue>"""
HINT = {
    "control": "",
    "oxide": "\nAn OXIDE code-search tool is available: mode `search` lists the code locations "
    "for a query, mode `context` returns their source.",
}
# Equal in both arms: no web (the fixes are public), no skills, no questions to a human.
DENY = {"webfetch": "deny", "websearch": "deny", "skill": "deny", "question": "deny"}


def config(arm: str, work: Path, data: Path) -> dict[str, Any]:
    cfg: dict[str, Any] = {"autoupdate": False, "permission": DENY}
    if arm == "oxide":
        command = [
            sys.executable,
            "-m",
            "oxide",
            "mcp",
            "--repo",
            str(work),
            "--data-dir",
            str(data),
        ]
        # codemode off: a direct tool call, not a script run through `execute`.
        server = {"type": "local", "command": command, "enabled": True, "codemode": False}
        cfg["mcp"] = {"oxide": server}
    return cfg


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


def agent_failure(status: str, events: str) -> str | None:
    if error := agent_error(events):
        return error
    return f"agent exited with {status}" if status not in {"ok", "timeout"} else None


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
    environment = {
        **os.environ,
        **env,
        "HOME": str(base / "home"),
        "PWD": str(work),
        "XDG_CONFIG_HOME": str(home),
        "XDG_DATA_HOME": str(base / "data"),
        "XDG_CACHE_HOME": str(base / "home" / ".cache"),
        "XDG_STATE_HOME": str(base / "home" / ".local" / "state"),
        "OXIDE_FTS_EXTENSION": str(fts_location()),
    }
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
    process = subprocess.Popen(
        command,
        cwd=work,
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


def isolation(arm: str, base: Path) -> None:
    """Reject runs that loaded MCPs other than the one explicitly configured for the arm."""
    log = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in (base / "data" / "opencode" / "log").glob("*.log")
    )
    connected = set(re.findall(r'message="mcp connected" server=(\S+)', log))
    expected = {"oxide"} if arm == "oxide" else set()
    if connected != expected:
        raise RuntimeError(f"isolation: unexpected MCP servers {sorted(connected)}")


def score(task: dict[str, Any], work: Path, env: dict[str, str]) -> dict[str, Any]:
    """Hidden tests on the agent's tree: its own test edits are reverted first."""
    for path in task["test_files"]:
        tracked = tasks.sh("git", "cat-file", "-e", f"HEAD:{path}", cwd=work).returncode == 0
        if tracked:
            tasks.sh("git", "checkout", "HEAD", "--", path, cwd=work)
        else:
            (work / path).unlink(missing_ok=True)
    tasks.apply(work, task["test_patch"])
    outcome = tasks.tests(work, env, task["test_files"])
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
    if arm != "oxide" or not log.exists():
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
    work, data, home = base / "work", base / "oxide-data", base / "config" / "opencode"
    row: dict[str, Any] = {
        "task": task["id"],
        "arm": arm,
        "rep": rep,
        "model": args.model,
        "split": args.split,
    }
    try:
        tasks.checkout(task, work)
        env = tasks.environment(task, work, base / "venv")
        home.mkdir(parents=True)
        (home / "opencode.jsonc").write_text(json.dumps(config(arm, work, data)), encoding="utf-8")
        if arm == "oxide":  # indexed before the clock starts; its cost is reported separately
            started = time.monotonic()
            indexed = tasks.sh(
                sys.executable,
                "-m",
                "oxide",
                "index",
                work,
                "--data-dir",
                data,
                env={"OXIDE_DATA_DIR": str(data)},
            )
            row["index_seconds"] = round(time.monotonic() - started, 2)
            if indexed.returncode:
                raise RuntimeError(f"oxide index: {indexed.stderr[-300:]}")
        credentials(args.model, base / "data")
        started = time.monotonic()
        row["status"], events = agent(args.model, arm, task, base, env, args.timeout)
        row["seconds"] = round(time.monotonic() - started, 1)
        (base / "events.jsonl").write_text(events, encoding="utf-8")
        row["events_sha256"] = protocol.sha256_bytes(events.encode())
        row |= measure(events)  # before any failure below: a failed run still spent tokens
        row["spend"] = protocol.spend(row["tokens"])
        row["oxide_tools_connected"] = connected(base / "data", arm)
        if failure := agent_failure(row["status"], events):
            raise RuntimeError(failure)
        isolation(arm, base)
        tasks.sh("git", "add", "-A", cwd=work)
        diff = tasks.sh("git", "diff", "--cached", "HEAD", "--", ".", ":!*.egg-info", cwd=work)
        (base / "patch.diff").write_text(diff.stdout, encoding="utf-8")
        row |= score(task, work, env)
    except (RuntimeError, subprocess.SubprocessError, OSError, sqlite3.Error) as error:
        row |= {"status": "harness-error", "error": str(error)[-400:], "resolved": None}
    row["trace"] = str(base.relative_to(tasks.CACHE))  # events, patch, data: kept, never pruned
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(base / "venv", ignore_errors=True)
    return row


def opencode_version() -> str:
    done = subprocess.run([str(OPENCODE), "--version"], capture_output=True, text=True, check=True)
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
                {a: config(a, placeholder, placeholder) for a in ARMS}, sort_keys=True
            ).encode()
        ),
        "deny": DENY,
        "opencode": opencode_version(),
        "harness_source_sha256": source_sha256(),
    }


def run_all(args: argparse.Namespace) -> None:
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
        for a in ARMS
        if (t["id"], a, r) not in done
    ]
    random.Random(0).shuffle(jobs)  # arms and tasks interleave: drift hits both alike
    lock = threading.Lock()
    spend_seen = [protocol.row_spend(r) for r in existing if protocol.row_has_spend(r)]
    state = {
        "spent": protocol.spent(existing),
        "skipped": 0,
        "in_flight": 0,
        "largest": max(spend_seen, default=None),
    }

    def work(job: tuple[dict[str, Any], str, int]) -> None:
        with lock:  # a run starts only if the cap still holds with every run in flight
            if not protocol.admit(
                state["spent"], state["in_flight"], state["largest"], args.max_tokens
            ):
                state["skipped"] += 1
                return
            state["in_flight"] += 1
        try:
            row = run_one(*job, args, root)
        except BaseException:
            with lock:
                state["in_flight"] -= 1
            raise
        with lock, out.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row) + "\n")
            state["in_flight"] -= 1  # same lock as the spend: the cap is never blind
            if "spend" in row:
                state["spent"] += row["spend"]
                state["largest"] = max(state["largest"] or 0, row["spend"])
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
