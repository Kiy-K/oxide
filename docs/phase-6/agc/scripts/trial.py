"""AGC agent trial (preregistration.md): one agent, four tool sets, the same tasks.

    trial.py check                 every truth/neutral key exists; checkouts digested
    trial.py prompt TASK ARM       print one run's system prompt and message
    trial.py run REP WORKERS       every task x arm once, in a seeded order, skipping done
    trial.py dev                   the unscored dev task, every arm (harness check)
    trial.py grade                 score every finished run -> results/runs.json
    trial.py verify                the checkouts still match the digests from `check`

Agent: OpenCode (`opencode run --standalone`), MODEL, an isolated config per run (no
user MCP servers, plugins, skills or instructions), read-only built-in tools (read, grep,
glob, list). Tool arms add `server.py` over MCP. OpenCode 2.1 reaches MCP tools only
from its code-mode `execute` tool and leaves them out of the catalog the model sees
(README § Harness), so a tool arm's system prompt carries each tool's signature and
description, as a schema would. The server logs every call.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from server import GRAPH, TIERS  # noqa: E402

OXIDE = HERE.parents[3]
EVAL = Path.home() / "Projects/oxide-eval-data"
DATA = Path(os.environ.get("OXIDE_AGC_DATA", EVAL / "oxide-agc-eval"))
REPOS = EVAL / "oxide-structural-trial/repos"
STORE = EVAL / "oxide-graphnav-eval/data"
RESULTS = HERE.parent / "results"
MODEL = "openai/gpt-6-luna"
SEED = 2026102501
TIMEOUT_S = 900
ARMS = {"B": (), "G": ("graph",), "T": ("tiers",), "AGC": ("graph", "tiers")}
PYTHON = str(OXIDE / ".venv/bin/python")
SYSTEM = (
    "You are a careful software engineer answering questions about the code in the "
    "current directory. Use your tools to inspect the code. Do not modify any file."
)
TOOL_DOCS = {
    "graph": "`await tools.agc.oxide_graph({symbol, direction, depth})` -> string. "
    "symbol: name, `Class.method`, dotted path or `path.py:line`; direction: "
    "'callers' (default) or 'callees'; depth: 1 (default) to 3. " + GRAPH,
    "tiers": "`await tools.agc.oxide_tiers({level, target})` -> string. level: "
    "'architecture', 'module', 'symbol' or 'source'; target: file path, name or "
    "path:start-end. " + TIERS,
}
MESSAGE = """You are in a checkout of the {repo} repository (the current directory). \
Answer the question below by inspecting the code. Do not modify any file.

{question}

End your reply with a line `ANSWER:` followed by one `path:line` per line \
(repository-relative path, 1-based line number) and nothing after them."""
DENY = ["bash", "edit", "write", "patch", "apply_patch", "multiedit", "webfetch", "websearch",
        "task", "todowrite", "todoread", "codesearch", "skill", "lsp", "question",
        "external_directory", "browser"]  # fmt: skip
ANSWER = re.compile(r"([\w./@+\-\[\]]+\.py):(\d+)")


def load() -> dict[str, Any]:
    return json.loads((HERE.parent / "tasks.json").read_text())


def tasks() -> list[dict[str, Any]]:
    return load()["tasks"]


def by_id() -> dict[str, dict[str, Any]]:
    return {t["id"]: t for t in [*tasks(), load()["dev"]]}


def system(arm: str) -> str:
    if not ARMS[arm]:
        return SYSTEM
    docs = "\n\n".join(TOOL_DOCS[t] for t in ARMS[arm])
    lead = (
        "Besides your built-in tools, the `execute` tool can call OXIDE functions on this "
        "checkout from JavaScript. Use them if they help."
    )
    return f"{SYSTEM}\n\n{lead}\n\n{docs}"


def configure(out: Path, arm: str, repo: str) -> dict[str, str]:
    permission = dict.fromkeys(DENY, "deny") | dict.fromkeys(
        ("read", "grep", "glob", "list"), "allow"
    )
    permission["execute"] = "allow" if ARMS[arm] else "deny"
    servers = {}
    if ARMS[arm]:
        command = [PYTHON, str(HERE / "server.py"), "--repo", str(REPOS / repo),
                   "--data-dir", str(STORE), "--tools", ",".join(ARMS[arm]),
                   "--log", str(out / "mcp.jsonl")]  # fmt: skip
        servers["agc"] = {"type": "local", "command": command, "disabled": False, "codemode": True}
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
        "LANG": "C.UTF-8",
        "PATH": "/usr/bin:" + str(Path.home() / ".bun/bin"),
    }


def limited(record: dict[str, Any]) -> bool:
    return any("rate limit" in json.dumps(e).lower() for e in record.get("errors", []))


def run_one(task: dict[str, Any], arm: str, rep: str) -> None:
    out = DATA / "runs" / rep / arm / task["id"]
    done_file = out / "record.json"
    if done_file.exists() and not limited(json.loads(done_file.read_text())):
        return
    out.mkdir(parents=True, exist_ok=True)
    env, cwd = configure(out, arm, task["repo"]), REPOS / task["repo"]
    message = MESSAGE.format(repo=task["repo"], question=task["question"])
    command = ["timeout", str(TIMEOUT_S), "opencode", "run", "--standalone", "--model", MODEL,
               "--agent", "trial", "--format", "json"]  # fmt: skip
    for attempt in range(12):  # rate limits are not evidence: wait and retry
        (out / "mcp.jsonl").unlink(missing_ok=True)
        began = time.time()
        done = subprocess.run(
            command, input=message, capture_output=True, text=True, env=env, cwd=cwd
        )
        events = []
        for line in done.stdout.splitlines():
            with contextlib.suppress(ValueError):
                events.append(json.loads(line))
        parts = [e.get("part", {}) for e in events]
        record: dict[str, Any] = {
            "task": task["id"], "arm": arm, "rep": rep, "model": MODEL, "attempt": attempt,
            "exit": done.returncode, "seconds": round(time.time() - began, 2),
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
                ["opencode", "session", "export", events[0]["sessionID"]],
                capture_output=True, text=True, env=env, cwd=cwd,
            )  # fmt: skip
            with contextlib.suppress(ValueError, KeyError):
                messages = json.loads(exported.stdout)["messages"]
                assistant = [m for m in messages if m.get("type") == "assistant"]
                record["usage"] = [m.get("tokens") for m in assistant]
                record["cost_usd"] = sum(m.get("cost") or 0 for m in assistant)
        log = out / "mcp.jsonl"
        record["mcp"] = (
            [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        )
        if not limited(record):
            break
        time.sleep(min(300, 60 * (attempt + 1)))
    done_file.write_text(json.dumps(record, indent=1) + "\n")
    print(task["id"], arm, rep, f"exit={record['exit']}", f"{record['seconds']}s", flush=True)


def cmd_run(rep: str, workers: int, todo: list[dict[str, Any]] | None = None) -> None:
    jobs = [(t, a) for t in (todo or tasks()) for a in ARMS]
    random.Random(f"{SEED}-{rep}").shuffle(jobs)
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(lambda job: run_one(job[0], job[1], rep), jobs))


# --- grading --------------------------------------------------------------------------
def answers(text: str) -> list[tuple[str, int]]:
    tail = text.rsplit("ANSWER:", 1)[1] if "ANSWER:" in text else ""
    return sorted({(p.removeprefix("./"), int(n)) for p, n in ANSWER.findall(tail)})


def key(item: str) -> str:
    return item if item.startswith("file:") else f"symbol:{item}"


def matches(claim: str, item: str, kind: str) -> bool:
    """The same entity, a member of it (`Request.__init__` for `Request`), or a function
    enclosing it (`test_x` for its nested `test_x.index`)."""
    nested = kind in ("function", "method") and item.startswith(claim + ".")
    return claim == item or claim.startswith(item + ".") or nested


def score(claims: set[str], task: dict[str, Any], kinds: dict[str, str]) -> dict[str, Any]:
    truth, neutral = [key(t) for t in task["truth"]], [key(n) for n in task["neutral"]]
    hit: set[str] = set()
    false = []
    for c in sorted(claims):
        kind = kinds.get(c, "")
        found = [t for t in truth if t not in hit and matches(c, t, kind)]
        if found:
            hit.add(found[0])
        elif not any(matches(c, t, kind) for t in truth + neutral):
            false.append(c)  # neither a truth item (nor a duplicate of one) nor neutral
    tp = len(hit)
    p = tp / (tp + len(false)) if tp + len(false) else 0.0
    r = tp / len(truth)
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "false_claims": len(false), "false": false, "missed": len(truth) - tp,
            "precision": p, "recall": r, "f1": f1}  # fmt: skip


def usage(record: dict[str, Any], root: Path) -> dict[str, Any]:
    steps = [u for u in record.get("usage") or [] if u]
    fresh = sum(u.get("input", 0) + u.get("output", 0) + u.get("reasoning", 0) for u in steps)
    cached = sum((u.get("cache") or {}).get(k, 0) for u in steps for k in ("read", "write"))
    built = [t for t in record["tools"] if t[0] != "execute"]
    executes = sum(t[0] == "execute" for t in record["tools"])
    mcp = record["mcp"]
    opened = [str((t[2] or {}).get("path") or (t[2] or {}).get("filePath", ""))
              for t in built if t[0] == "read"]  # fmt: skip
    opened += [m["args"]["target"].split(":")[0] for m in mcp
               if m["tool"] == "tiers" and m["args"]["level"] in ("module", "source")]  # fmt: skip
    opened = [o.removeprefix(f"{root}/").removeprefix("./") for o in opened if o]
    return {
        "tokens": fresh + cached, "tokens_uncached": fresh, "steps": len(steps),
        "tool_calls": len(built) + max(executes, len(mcp)),
        "builtin_calls": len(built), "oxide_calls": len(mcp),
        "graph_calls": sum(m["tool"] == "graph" for m in mcp),
        "tiers_calls": sum(m["tool"] == "tiers" for m in mcp),
        "reads": len(opened), "repeated_reads": len(opened) - len(set(opened)),
        "oxide_chars": sum(m["chars"] for m in mcp), "cost_usd": record.get("cost_usd"),
    }  # fmt: skip


def cmd_grade() -> None:
    from agc import Agc

    known = by_id()
    agcs = {r: Agc(REPOS / r, STORE) for r in {t["repo"] for t in known.values()}}
    rows = []
    for path in sorted((DATA / "runs").glob("*/*/*/record.json")):
        record = json.loads(path.read_text())
        task = known[record["task"]]
        agc = agcs[task["repo"]]
        found = answers(record["result"])
        claims = set()
        for p, n in found:
            owner = agc.resolve(f"{p}:{n}") if p in agc.files else []
            claims.add(owner[0] if owner else (f"file:{p}" if p in agc.files else f"?:{p}"))
        kinds = {k: agc.info[k][2] for k in claims if k in agc.info}
        row = {k: record[k] for k in ("task", "arm", "rep", "seconds", "exit")}
        row |= {"type": task["type"], "answers": len(found),
                "technical_failure": record["is_error"] or not found}  # fmt: skip
        rows.append(row | usage(record, REPOS / task["repo"]) | score(claims, task, kinds))
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "runs.json").write_text(json.dumps(rows, indent=1) + "\n")
    print(len(rows), "runs graded")


def digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(root)).encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def cmd_check() -> None:
    from agc import Agc

    for t in by_id().values():
        agc = Agc(REPOS / t["repo"], STORE)
        missing = [i for i in t["truth"] + t["neutral"] if key(i) not in agc.info]
        assert not missing, (t["id"], missing)
    sums = {r: digest(REPOS / r) for r in ("flask", "requests", "pytest")}
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "digests.json").write_text(json.dumps(sums, indent=1) + "\n")
    print("keys ok;", sums)


def cmd_verify() -> None:
    for repo, value in json.loads((DATA / "digests.json").read_text()).items():
        print(repo, "unchanged" if digest(REPOS / repo) == value else "CHANGED")


def main() -> None:
    command = sys.argv[1]
    if command == "check":
        cmd_check()
    elif command == "prompt":
        task = by_id()[sys.argv[2]]
        print(system(sys.argv[3]), "\n---\n", MESSAGE.format(**task))
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
