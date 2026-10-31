"""AGC v2 agent trial (preregistration.md): one agent, four matched arms, fresh tasks.

    trial.py check                 every truth/neutral key exists; checkouts digested
    trial.py prompt TASK ARM       print one run's system prompt and message
    trial.py run REP WORKERS       every task x arm once, in a seeded order, skipping done
    trial.py dev                   the unscored dev task, every arm (harness check)
    trial.py grade                 score every finished run -> results/runs.json
    trial.py verify                the checkouts still match the digests from `check`

Every run starts a private `opencode serve` with an isolated config, waits until the
location is loaded and any MCP server reports `connected`, then sends the message with
`opencode run --server`. OpenCode 2.1 does not wait for MCP servers before the first
model step, so without the wait a direct (non code-mode) tool is missing from step 1
(README § Harness). All arms take the same path. Grading is AGC v1's, unchanged.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import random
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v1 = _load("agc1_trial", HERE.parents[1] / "agc/scripts/trial.py")  # grading, digests
CONTEXT = _load("agc2_server", HERE / "server.py").CONTEXT

OXIDE = HERE.parents[3]
EVAL = Path.home() / "Projects/oxide-eval-data"
DATA = Path(os.environ.get("OXIDE_AGC2_DATA", EVAL / "oxide-agc2-eval"))
REPOS, STORE = v1.REPOS, v1.STORE
RESULTS = HERE.parent / "results"
MODEL = v1.MODEL
SEED = 2026102502
TIMEOUT_S = 900
# server: None, "direct" (codemode off: a native tool) or "codemode" (through `execute`)
ARMS = {
    "B": {"execute": False, "server": None},
    "Bcm": {"execute": True, "server": None},
    "Ad": {"execute": False, "server": "direct"},
    "Acm": {"execute": True, "server": "codemode"},
}
SYSTEM = (
    "You are a careful software engineer answering questions about the code in the "
    "current directory. Use your tools to inspect the code. Do not modify any file. "
    "Work efficiently: search with grep or glob before reading, read only the lines "
    "you need (use an offset and limit rather than whole files), do not re-read what "
    "you have already seen, and cite only code you have seen."
)
CODEMODE_DOC = (
    "Besides your built-in tools, the `execute` tool can call an OXIDE function on this "
    "checkout from JavaScript: `await tools.agc.oxide_context({target, relation, depth})` "
    "-> string. target: symbol name, `Class.method`, dotted path or `path.py:line`, a "
    "file path, or '' for the package map; relation: '' (default), 'callers' or "
    "'callees'; depth: 1 (default) to 3. Use it if it helps.\n\n" + CONTEXT
)
DENY = v1.DENY
# price ratios of GPT-5-class list prices (input 1, cached input 0.1, output 8)
WEIGHTS = {"input": 1.0, "cache_read": 0.1, "cache_write": 1.0, "output": 8.0}


def load() -> dict[str, Any]:
    return json.loads((HERE.parent / "tasks.json").read_text())


def tasks() -> list[dict[str, Any]]:
    return load()["tasks"]


def by_id() -> dict[str, dict[str, Any]]:
    return {t["id"]: t for t in [*tasks(), load()["dev"]]}


def system(arm: str) -> str:
    return f"{SYSTEM}\n\n{CODEMODE_DOC}" if ARMS[arm]["server"] == "codemode" else SYSTEM


def configure(out: Path, arm: str, repo: str) -> dict[str, str]:
    spec = ARMS[arm]
    permission = dict.fromkeys(DENY, "deny") | dict.fromkeys(
        ("read", "grep", "glob", "list"), "allow"
    )
    permission["execute"] = "allow" if spec["execute"] else "deny"
    servers = {}
    if spec["server"]:
        permission["agc_*"] = "allow"
        command = [v1.PYTHON, str(HERE / "server.py"), "--repo", str(REPOS / repo),
                   "--data-dir", str(STORE), "--log", str(out / "mcp.jsonl")]  # fmt: skip
        servers["agc"] = {"type": "local", "command": command,
                          "codemode": spec["server"] == "codemode"}  # fmt: skip
    agent = {"mode": "primary", "prompt": system(arm), "permission": permission}
    config = {"mcp": {"servers": servers}, "plugin": [], "instructions": [],
              "permission": permission, "agent": {"trial": agent}}  # fmt: skip
    path = out / "config/opencode/opencode.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=1) + "\n")
    return {
        "HOME": os.environ["HOME"],  # the provider credentials live in the user data dir
        "XDG_CONFIG_HOME": str(out / "config"),
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_PASSWORD": secrets.token_hex(16),
        "LANG": "C.UTF-8",
        "PATH": "/usr/bin:" + str(Path.home() / ".bun/bin"),
    }


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def ready(url: str, env: dict[str, str], cwd: Path, want_mcp: bool) -> bool:
    """The location is loaded and, if the arm has a server, it reports connected."""
    query = urllib.parse.urlencode({"location[directory]": str(cwd)})
    auth = b64encode(f"opencode:{env['OPENCODE_PASSWORD']}".encode()).decode()
    request = urllib.request.Request(f"{url}/api/mcp?{query}",
                                     headers={"Authorization": f"Basic {auth}"})  # fmt: skip
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            servers = json.load(reply)["data"]
    except (OSError, urllib.error.URLError, ValueError, KeyError):
        return False
    status = [s.get("status", {}).get("status") for s in servers]
    return status == ["connected"] if want_mcp else True


def limited(record: dict[str, Any]) -> bool:
    return bool(v1.limited(record))


def attempt(task: dict[str, Any], arm: str, rep: str, out: Path) -> dict[str, Any]:
    env, cwd = configure(out, arm, task["repo"]), REPOS / task["repo"]
    url = f"http://127.0.0.1:{free_port()}"
    message = v1.MESSAGE.format(repo=task["repo"], question=task["question"])
    (out / "mcp.jsonl").unlink(missing_ok=True)
    serve = subprocess.Popen(
        ["opencode", "serve", "--hostname", "127.0.0.1", "--port", url.rsplit(":", 1)[1]],
        env=env, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    record: dict[str, Any] = {"task": task["id"], "arm": arm, "rep": rep, "model": MODEL}
    try:
        began = time.time()
        while not ready(url, env, cwd, bool(ARMS[arm]["server"])):
            if time.time() - began > 90 or serve.poll() is not None:
                return record | {"is_error": True, "errors": ["server not ready"],
                                 "result": "", "tools": [], "mcp": [], "exit": -1,
                                 "seconds": 0.0}  # fmt: skip
            time.sleep(0.5)
        record["startup_s"] = round(time.time() - began, 2)
        command = ["timeout", str(TIMEOUT_S), "opencode", "run", "--server", url, "--model",
                   MODEL, "--agent", "trial", "--format", "json"]  # fmt: skip
        began = time.time()
        done = subprocess.run(
            command, input=message, capture_output=True, text=True, env=env, cwd=cwd
        )
        seconds = round(time.time() - began, 2)
        events = []
        for line in done.stdout.splitlines():
            with contextlib.suppress(ValueError):
                events.append(json.loads(line))
        parts = [e.get("part", {}) for e in events]
        record |= {
            "exit": done.returncode, "seconds": seconds,
            "result": "".join(p.get("text", "") for p in parts if p.get("type") == "text"),
            "tools": [[p.get("tool"), p.get("state", {}).get("status"),
                       p.get("state", {}).get("input")] for p in parts if p.get("type") == "tool"],
            "errors": [e.get("error") for e in events if e.get("type") == "error"],
            "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
            "system_sha256": hashlib.sha256(system(arm).encode()).hexdigest(),
        }  # fmt: skip
        record["is_error"] = bool(record["errors"]) or not events or done.returncode != 0
        if not events:
            record["stderr"] = done.stderr[-2000:]
        if events and not record["is_error"]:
            exported = subprocess.run(
                ["opencode", "session", "export", events[0]["sessionID"], "--server", url],
                capture_output=True, text=True, env=env, cwd=cwd,
            )  # fmt: skip
            with contextlib.suppress(ValueError, KeyError):
                messages = json.loads(exported.stdout)["messages"]
                assistant = [m for m in messages if m.get("type") == "assistant"]
                record["usage"] = [m.get("tokens") for m in assistant]
                record["cost_usd"] = sum(m.get("cost") or 0 for m in assistant)
    finally:
        serve.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            serve.wait(10)
        if serve.poll() is None:
            serve.kill()
    log = out / "mcp.jsonl"
    record["mcp"] = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return record


def run_one(task: dict[str, Any], arm: str, rep: str) -> None:
    out = DATA / "runs" / rep / arm / task["id"]
    done_file = out / "record.json"
    if done_file.exists() and not limited(json.loads(done_file.read_text())):
        return
    out.mkdir(parents=True, exist_ok=True)
    for n in range(12):  # rate limits are not evidence: wait and retry
        record = attempt(task, arm, rep, out) | {"attempt": n}
        if not limited(record):
            break
        time.sleep(min(300, 60 * (n + 1)))
    done_file.write_text(json.dumps(record, indent=1) + "\n")
    print(task["id"], arm, rep, f"exit={record['exit']}", f"{record['seconds']}s", flush=True)


def cmd_run(rep: str, workers: int, todo: list[dict[str, Any]] | None = None) -> None:
    jobs = [(t, a) for t in (todo or tasks()) for a in ARMS]
    random.Random(f"{SEED}-{rep}").shuffle(jobs)
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(lambda job: run_one(job[0], job[1], rep), jobs))


# --- grading --------------------------------------------------------------------------
def usage(record: dict[str, Any], root: Path) -> dict[str, Any]:
    steps = [u for u in record.get("usage") or [] if u]
    part = {
        "input": sum(u.get("input", 0) for u in steps),
        "output": sum(u.get("output", 0) + u.get("reasoning", 0) for u in steps),
        "cache_read": sum((u.get("cache") or {}).get("read", 0) for u in steps),
        "cache_write": sum((u.get("cache") or {}).get("write", 0) for u in steps),
    }
    first = steps[0] if steps else {}
    built = [t for t in record["tools"] if t[0] in ("read", "grep", "glob", "list")]
    wrapped = sum(t[0] == "execute" or str(t[0]).startswith("agc_") for t in record["tools"])
    mcp = record["mcp"]
    opened = [str((t[2] or {}).get("path") or (t[2] or {}).get("filePath", ""))
              for t in built if t[0] == "read"]  # fmt: skip
    opened = [o.removeprefix(f"{root}/").removeprefix("./") for o in opened if o]
    return {
        "tokens": sum(part.values()), "tokens_uncached": part["input"] + part["output"],
        "cost_units": round(sum(WEIGHTS[k] * v for k, v in part.items()), 1),
        "steps": len(steps),
        "first_prompt": first.get("input", 0) + sum((first.get("cache") or {}).values()),
        "tool_calls": len(built) + max(wrapped, len(mcp)),
        "builtin_calls": len(built), "execute_calls": wrapped, "oxide_calls": len(mcp),
        "grep_calls": sum(t[0] in ("grep", "glob") for t in built),
        "read_calls": sum(t[0] == "read" for t in built),
        "graph_calls": sum(bool(m["args"]["relation"]) for m in mcp),
        "outline_calls": sum(not m["args"]["relation"] for m in mcp),
        "reads": len(opened), "repeated_reads": len(opened) - len(set(opened)),
        "oxide_chars": sum(m["chars"] for m in mcp), "oxide_ms": sum(m["ms"] for m in mcp),
        "cost_usd": record.get("cost_usd"), "startup_s": record.get("startup_s"),
    }  # fmt: skip


def cmd_grade() -> None:
    from agc import Agc  # AGC v1's, on the path through `v1`

    known = by_id()
    agcs = {r: Agc(REPOS / r, STORE) for r in {t["repo"] for t in known.values()}}
    rows = []
    for path in sorted((DATA / "runs").glob("*/*/*/record.json")):
        record = json.loads(path.read_text())
        task = known[record["task"]]
        agc = agcs[task["repo"]]
        found = v1.answers(record["result"])
        claims = set()
        for p, n in found:
            owner = agc.resolve(f"{p}:{n}") if p in agc.files else []
            claims.add(owner[0] if owner else (f"file:{p}" if p in agc.files else f"?:{p}"))
        kinds = {k: agc.info[k][2] for k in claims if k in agc.info}
        row = {k: record.get(k) for k in ("task", "arm", "rep", "seconds", "exit")}
        failed = bool(record["is_error"]) or not found
        row |= {"type": task["type"], "answers": len(found), "technical_failure": failed}
        row |= usage(record, REPOS / task["repo"]) | v1.score(claims, task, kinds)
        row["success"] = not failed and row["f1"] >= 0.8 and row["false_claims"] == 0
        rows.append(row)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "runs.json").write_text(json.dumps(rows, indent=1) + "\n")
    print(len(rows), "runs graded")


def cmd_check() -> None:
    from agc import Agc

    for t in by_id().values():
        agc = Agc(REPOS / t["repo"], STORE)
        missing = [i for i in t["truth"] + t["neutral"] if v1.key(i) not in agc.info]
        assert not missing, (t["id"], missing)
    sums = {r: v1.digest(REPOS / r) for r in ("flask", "requests", "pytest")}
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "digests.json").write_text(json.dumps(sums, indent=1) + "\n")
    print("keys ok;", sums)


def cmd_verify() -> None:
    for repo, value in json.loads((DATA / "digests.json").read_text()).items():
        print(repo, "unchanged" if v1.digest(REPOS / repo) == value else "CHANGED")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "check":
        cmd_check()
    elif command == "prompt":
        task = by_id()[sys.argv[2]]
        print(system(sys.argv[3]), "\n---\n", v1.MESSAGE.format(**task))
    elif command == "run":
        cmd_run(f"r{sys.argv[2]}", int(sys.argv[3]))
    elif command == "dev":
        cmd_run("dev", 2, [load()["dev"]])
    elif command == "grade":
        cmd_grade()
    elif command == "verify":
        cmd_verify()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
