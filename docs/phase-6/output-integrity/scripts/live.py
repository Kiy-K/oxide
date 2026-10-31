"""Live MCP stage (preregistration.md § Live MCP stage): what a real client delivers.

    live.py run WORKERS     every batch: (re)start an isolated OpenCode server whose only
                            MCP server is `oxide` (production or variant_mcp.py), then one
                            agent call per task and mode -> DATA/live/<batch>/<task>~<mode>.json
    live.py grade           -> results/live.json

The agent is told to call `oxide_search` once and report what it received. What the
model saw is the tool part OpenCode recorded (its own clipping included); the server's
complete output is rebuilt in-process from the same store.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpora import DATA, STORE, mcp_query, tasks
from render import more, plain

import oxide
from oxide.mcp_server import MAX_BUDGET, MAX_RESULTS

REPO = Path(__file__).resolve().parents[4]
RESULTS = Path(__file__).resolve().parents[1] / "results"
LIVE = DATA / "live"
OPENCODE = DATA / "opencode-live"
PORT = 47812
MODEL = "openai/gpt-6-luna"
BATCHES = (  # (client path, arm, modes)
    ("direct", "C", ("search", "context")), ("direct", "CM", ("search",)),
    ("direct", "CL", ("search",)), ("direct", "E", ("search", "context")),
    ("code", "C", ("search", "context")), ("code", "CM", ("search",)),
    ("code", "E", ("context",)),
)  # fmt: skip
PROMPT = """You are working on the repository at {root}. Call the `oxide_search` tool of the \
`oxide` MCP server exactly once, with repository "{root}", mode "{mode}", {size}, and the \
issue text below as the query. Do not call any other tool and do not open or read any file.

Then reply with only JSON:
{{"cut": <true if the tool output you received was cut off, clipped or damaged before you \
saw all of it, else false>, "more": <true if the tool itself reported {more}, false if it \
reported none, null if you could not tell>, "count": <how many {unit} you could completely \
see>, "first_file": <the source file path of the first {first}, or null if you could not see \
it>}}

<issue>
{issue}
</issue>
"""
ENV = {
    "HOME": os.environ["HOME"],
    "XDG_CONFIG_HOME": str(OPENCODE / "config"),
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "LANG": "C.UTF-8",
    "PATH": "/usr/bin:" + str(Path(os.environ.get("OPENCODE_BIN", "/usr/bin/opencode")).parent),
}


def configure(path: str, arm: str, roots: list[str]) -> None:
    if arm == "C":
        command = [str(REPO / ".venv/bin/oxide"), "mcp"]
    else:
        variant = str(Path(__file__).resolve().parent / "variant_mcp.py")
        command = [str(REPO / ".venv/bin/python"), variant, arm]
    for root in roots:
        command += ["--repo", root]
    command += ["--data-dir", str(STORE)]
    server = {"type": "local", "command": command, "codemode": path == "code"}
    agent = {"mode": "primary", "prompt": "You call tools when asked and report faithfully."}
    config = {"mcp": {"servers": {"oxide": server}}, "plugin": [], "instructions": [],
              "agent": {"live": agent}}  # fmt: skip
    file = OPENCODE / "config/opencode/opencode.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(config, indent=1) + "\n")
    (OPENCODE / "home").mkdir(exist_ok=True)


def serve() -> str:
    """(Re)start the isolated server on PORT; returns its password."""
    listing = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout
    for line in listing.splitlines():
        pid, _, args = line.strip().partition(" ")
        if "opencode" in args.split(" ")[0] and f"serve --hostname 127.0.0.1 --port {PORT}" in args:
            os.kill(int(pid), 15)
    for _ in range(30):  # until the old server has released the port
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", PORT)) != 0:
                break
        time.sleep(1)
    log = OPENCODE / "serve.log"
    subprocess.Popen(
        ["opencode", "serve", "--hostname", "127.0.0.1", "--port", str(PORT)],
        env=ENV, cwd=OPENCODE / "home", stdout=log.open("w"), stderr=subprocess.STDOUT,
        start_new_session=True,
    )  # fmt: skip
    for _ in range(60):
        time.sleep(1)
        found = re.search(r"server password (\S+)", log.read_text())
        if found:
            time.sleep(8)  # let the MCP server connect
            return found.group(1)
    raise RuntimeError("opencode serve did not start")


def oxide_call(record: dict[str, Any]) -> dict[str, Any] | None:
    """The tool part that ran oxide_search: direct, or inside code mode's ``execute``."""
    for t in record["tools"]:
        meta = t["metadata"].get("metadata") or {}
        direct = t["tool"] == "oxide_oxide_search"
        coded = t["tool"] == "execute" and any(
            c.get("tool", "").endswith("oxide_search") and c.get("status") == "completed"
            for c in meta.get("toolCalls", [])
        )  # fmt: skip
        if (direct or coded) and t["status"] == "completed":
            return t
    return None


def call(prompt: str, password: str, out: Path) -> None:
    command = ["timeout", "600", "opencode", "run", "--server", f"http://127.0.0.1:{PORT}",
               "--model", MODEL, "--agent", "live", "--format", "json"]  # fmt: skip
    env = ENV | {"OPENCODE_SERVER_PASSWORD": password}
    for attempt in range(4):  # retried only when the oxide tool never ran
        began = time.time()
        done = subprocess.run(command, input=prompt, capture_output=True, text=True, env=env,
                              cwd=OPENCODE / "home")  # fmt: skip
        events = []
        for line in done.stdout.splitlines():
            with contextlib.suppress(ValueError):
                events.append(json.loads(line))
        parts = [e.get("part", {}) for e in events]
        record: dict[str, Any] = {
            "exit": done.returncode, "seconds": round(time.time() - began, 2), "attempt": attempt,
            "result": "".join(p.get("text", "") for p in parts if p.get("type") == "text"),
            "tools": [], "session": events[0].get("sessionID") if events else None,
        }  # fmt: skip
        for p in parts:
            if p.get("type") == "tool":
                state = p.get("state", {})
                meta = {k: v for k, v in (state.get("metadata") or {}).items() if k != "content"}
                record["tools"].append({"tool": p.get("tool"), "status": state.get("status"),
                                        "input": state.get("input"), "output": state.get("output"),
                                        "metadata": meta})  # fmt: skip
        if record["session"]:
            exported = subprocess.run(["opencode", "session", "export", record["session"]],
                                      capture_output=True, text=True, env=env,
                                      cwd=OPENCODE / "home")  # fmt: skip
            with contextlib.suppress(ValueError, KeyError):
                messages = json.loads(exported.stdout)["messages"]
                record["tokens"] = [
                    m.get("tokens") for m in messages if m.get("type") == "assistant"
                ]
        if oxide_call(record) is not None:
            break
    out.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n")


def jobs() -> list[tuple[str, str, str]]:
    """(task id, mode, prompt) for every task and mode."""
    words = {"search": ("that more matching results exist beyond its result limit",
                        "result entries", "result entry"),
             "context": ("omitting candidate code to fit its size budget",
                         "`### ` source sections", "`### ` source section")}  # fmt: skip
    out = []
    for task, root in tasks():
        for mode in ("search", "context"):
            size = f"limit {MAX_RESULTS}" if mode == "search" else f"budget {MAX_BUDGET}"
            reported, unit, first = words[mode]
            prompt = PROMPT.format(root=root, mode=mode, size=size, more=reported, unit=unit,
                                   first=first, issue=mcp_query(task))  # fmt: skip
            out.append((task["id"], mode, prompt))
    return out


def run(workers: int) -> None:
    roots = sorted({str(root) for _, root in tasks()})
    for path, arm, modes in BATCHES:
        batch = LIVE / f"{path}-{arm}"
        batch.mkdir(parents=True, exist_ok=True)
        todo = [(prompt, batch / f"{tid}~{mode}.json") for tid, mode, prompt in jobs()
                if mode in modes and not (batch / f"{tid}~{mode}.json").exists()]  # fmt: skip
        if not todo:
            continue
        configure(path, arm, roots)
        password = serve()
        print(f"{batch.name}: {len(todo)} calls", file=sys.stderr, flush=True)
        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(lambda job: call(job[0], password, job[1]), todo))


# --- grading ---

STATUS = {"search": ('"truncated"', '"more_available"', "# oxide search:"),
          "context": ("# oxide context:", '"omissions"')}  # fmt: skip


def sent(used: dict[str, Any]) -> dict[str, Any]:
    """The arguments the agent actually passed to oxide_search (it may edit the query)."""
    calls = ((used.get("metadata") or {}).get("metadata") or {}).get("toolCalls") or []
    coded = [c["input"] for c in calls if c.get("tool", "").endswith("oxide_search")]
    return coded[0] if coded else used.get("input") or {}


def server_truth(args: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any]:
    """What the server returned for ``args``, rebuilt in-process from the same store."""
    key = json.dumps(args, sort_keys=True)
    if key not in cache:
        with oxide.open_repository(args["repository"], data_dir=STORE, create=False) as repo:
            hints = {k: tuple(args.get(k) or ()) for k in ("paths", "symbols")}
            if args.get("mode") == "context":
                b = plain(repo.build_context(args["query"], args.get("budget", 2048), **hints))
                first = b["items"][0]["source"]["path"] if b["items"] else None
                cache[key] = {"more": bool(b["omissions"]), "count": len(b["items"]), "first": first}
            else:
                v = plain(repo.search(args["query"], limit=args.get("limit", 20), **hints))
                src = v["entries"][0]["entity"]["source"] if v["entries"] else None
                cache[key] = {"more": more(v), "count": len(v["entries"]),
                              "first": src["path"] if src else None}  # fmt: skip
    return dict(cache[key])


def grade() -> None:
    cache: dict[str, Any] = {}
    queries = {task["id"]: mcp_query(task) for task, _ in tasks()}
    rows = []
    for path, arm, modes in BATCHES:
        for tid, mode, _ in jobs():
            file = LIVE / f"{path}-{arm}" / f"{tid}~{mode}.json"
            if mode not in modes or not file.exists():
                continue
            record = json.loads(file.read_text())
            used = oxide_call(record)
            seen = (used or {}).get("output") or ""
            meta = ((used or {}).get("metadata") or {}).get("metadata") or {}
            clipped = bool(meta.get("truncated")) or "[showing " in seen[-300:]
            others = [t["tool"] for t in record["tools"]
                      if t is not used and t["status"] == "completed"]  # fmt: skip
            found = re.search(r"\{.*\}", record["result"], re.S)
            answer = None
            with contextlib.suppress(ValueError, AttributeError):
                answer = json.loads(found.group(0)) if found else None
            args = sent(used) if used else {}
            unknown = {"more": None, "count": None, "first": None}
            t = server_truth(args, cache) if args.get("repository") else unknown
            t["query_edited"] = bool(args) and args.get("query") != queries[tid]
            t["args"] = {k: v for k, v in args.items() if k != "query"}
            status_seen = any(s in seen for s in STATUS[mode])
            first_seen = t["first"] is not None and t["first"] in seen
            gold = {"cut": clipped, "more": t["more"] if status_seen else None,
                    "first_file": t["first"] if first_seen else None}  # fmt: skip
            ok = isinstance(answer, dict)
            tokens = [x for x in record.get("tokens") or [] if x]
            rows.append({
                "task": tid, "mode": mode, "path": path, "arm": arm, "called": used is not None,
                "other_tools": others, "attempts": record["attempt"] + 1,
                "seconds": record["seconds"], "delivered_bytes": len(seen.encode()),
                "clipped": clipped, "marker": re.findall(r"\[showing [^\]]*?of \d+", seen[-300:]),
                "status_seen": status_seen, "first_seen": first_seen, "server": t, "gold": gold,
                "answer": answer if ok else None, "valid": ok,
                "correct": {k: ok and answer.get(k) == g for k, g in gold.items()},
                "prompt_tokens": sum(x.get("input", 0) + (x.get("cache") or {}).get("read", 0)
                                     for x in tokens),
                "code": ((used or {}).get("input") or {}).get("code") if path == "code" else None,
            })  # fmt: skip
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "live.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    print(len(rows), "live calls graded")


if __name__ == "__main__":
    if sys.argv[1] == "run":
        run(int(sys.argv[2]))
    else:
        grade()
