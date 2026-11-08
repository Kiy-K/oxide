"""One agent run: a clean checkout, the arm's tools, the agent, then the hidden tests.

The task's editable project and tests are present; agent edits are discarded before hidden tests.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import arms
import batch
import codex
import confine
import events
import protocol
import session
import tasks

from oxide.storage.fts import fts_location as fts_location

USER_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


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


def _credential_node(value: Any) -> Any:
    """A credential cell is often JSON; redaction needs the nested tokens, not the blob."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value
        if isinstance(parsed, (dict, list)):
            return parsed
    return value


def credential_values(db: Path) -> list[str]:
    """The provider key in a run's kept OpenCode copy, so its events can be redacted."""
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT * FROM credential").fetchall()
    secrets: list[str] = []
    for row in rows:
        for value in row:
            secrets.extend(confine.secret_values(_credential_node(value)))
    return secrets


def forget_credentials(db: Path) -> None:
    """Traces are kept, so the run's copy of the provider key is removed once the agent ends.
    VACUUM rewrites the file: a DELETE alone leaves the key bytes in free pages."""
    if db.is_symlink():  # the agent planted it: opening it would rewrite the host's own database
        db.unlink()
        return
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        conn.execute("DELETE FROM credential")
        conn.execute("VACUUM")
    finally:
        conn.close()


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
    prompt = arms.PROMPT.format(hint=arms.HINT[arm], problem=task["problem"].strip())
    db = base / "data" / "opencode" / "opencode.db"
    try:  # the key must be forgotten even if a step before the launch fails
        secrets = credential_values(db)
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
            str(arms.OPENCODE),
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
        status, raw = launch(wrapped, work, environment, timeout)
    finally:
        forget_credentials(db)
    return status, confine.redact(raw, secrets)


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


def run_one(
    task: dict[str, Any], arm: str, rep: int, args: argparse.Namespace, root: Path
) -> dict[str, Any]:
    key = f"{task['id']}.{arm}.{rep}"
    base = root / key
    work, data = base / "work", base / "oxide-data"
    using_codex = args.runner == "codex"
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
        if not using_codex:
            home = base / "config" / "opencode"
            home.mkdir(parents=True)
            cfg = arms.config(arm, work, data, args.base_src)
            (home / "opencode.jsonc").write_text(json.dumps(cfg), encoding="utf-8")
        if arm in arms.OXIDE_ARMS:  # cost of indexing is reported separately
            started = time.monotonic()
            indexed = tasks.sh(
                *arms.oxide_command(arm, args.base_src, "index", work, "--data-dir", data),
                env={"OXIDE_DATA_DIR": str(data)},
            )
            row["index_seconds"] = round(time.monotonic() - started, 2)
            if indexed.returncode:
                raise RuntimeError(f"oxide index: {indexed.stderr[-300:]}")
        if using_codex:
            started = time.monotonic()
            command = arms.mcp_command(arm, work, data, args.base_src)
            row["status"], raw = codex.codex_agent(
                args.model, arm, task, base, env, args.timeout, command
            )
        else:
            credentials(args.model, base / "data")
            started = time.monotonic()
            row["status"], raw = agent(args.model, arm, task, base, env, args.timeout)
        row["seconds"] = round(time.monotonic() - started, 1)
        # before any trace write or failure below: a failed run still spent tokens
        row |= (codex.codex_measure if using_codex else events.measure)(raw)
        row["spend"] = protocol.spend(row["tokens"])
        session.write_private(base / "events.jsonl", raw)
        row["events_sha256"] = protocol.sha256_bytes(raw.encode())
        row["oxide_tools_connected"] = (
            codex.codex_connected(arm) if using_codex else session.connected(base / "data", arm)
        )
        reason = codex.codex_agent_error if using_codex else events.agent_error
        if failure := events.agent_failure(row["status"], raw, reason):
            raise RuntimeError(failure)
        if using_codex:
            codex.codex_isolation(arm, raw)
        else:
            session.isolation(arm, base)
        session.git_on_agent(base, "add", "-A", cwd=work)
        diff = session.git_on_agent(
            base,
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
        session.write_private(base / "patch.diff", diff.stdout)
        row |= session.score(task, work, env, base)
    except (RuntimeError, subprocess.SubprocessError, OSError, sqlite3.Error) as error:
        row |= {"status": "harness-error", "error": str(error)[-400:], "resolved": None}
    row["trace"] = str(base.relative_to(tasks.CACHE))  # events, patch, data: kept, never pruned
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(base / "venv", ignore_errors=True)
    return row


def run_all(args: argparse.Namespace) -> None:
    confine.require()
    if "oxide-base" in args.arms:
        if args.base_src is None:
            raise SystemExit(
                "oxide-base needs --base-src: the snapshot whose tool description it runs"
            )
        arms.check_base_src(args.base_src)
    pool = sorted(tasks.load(args.split), key=tasks.order)
    chosen = [t for t in pool if not args.only or t["id"] in args.only][: args.tasks]
    root = tasks.CACHE / "runs" / time.strftime("%Y%m%dT%H%M%S")
    out = args.out or tasks.CACHE / "results" / f"{root.name}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    protocol.check_manifest(
        out.with_name(out.stem + ".manifest.json"), batch.manifest(args, chosen)
    )
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
