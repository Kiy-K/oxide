"""Navigation stage (preregistration.md § Stage 2): which lines does an agent choose to
read, given candidate files and one kind of file overview?

    nav.py sizes CASES...                    prompt sizes per arm (no model call)
    nav.py run REP ARMS WORKERS CASES...     one call per case x arm, skipping done ones
    nav.py grade OUT.json CASES...           score every finished call

Arms differ only in the per-file overview: N path and length, A OXIDE's current file
view (a hinted file's header view), F the whole numbered file, B the ast-grep outline,
C the native outline. The agent is OpenCode (`opencode run`) with a fixed model and a
custom agent whose system prompt is SYSTEM and which has no tools (every tool disabled
and denied), an isolated config (no MCP, plugins, instructions or Claude Code
compatibility) written by `configure()`, an empty working directory, one turn. Grading is deterministic: the chosen ranges are read
in order, up to MAX_LINES lines, and verified gold lines read count. At a total budget
T, overview units plus read units may not exceed T (the overview is paid for).
"""

from __future__ import annotations

import contextlib
import json
import os
import random
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from outline import DATA  # noqa: E402

from oxide.core.packing import units  # noqa: E402

MODEL = "openai/gpt-6-luna"
SEED = 20261019
MAX_RANGES, MAX_LINES = 8, 200
BUDGETS = (2048, 4096)
TIMEOUT_S = 600
SYSTEM = "You decide which source lines a developer should read. Answer with JSON only."
PROMPT = """An issue was reported in a code repository. Below are candidate files: each \
with its path, its length in lines and an overview, which may be empty. You cannot open \
the files. Choose the line ranges a developer should read to find the code that must \
change for this issue and the code that change depends on.

Rules: at most {ranges} ranges and at most {lines} lines in total, most important first; \
1-based, inclusive line numbers.

Reply with only JSON: {{"ranges": [{{"path": "<path>", "start": <int>, "end": <int>}}]}}

<issue>
{issue}
</issue>

<files>
{files}</files>
"""
OPENCODE = DATA / "opencode"
ENV = {
    "HOME": os.environ["HOME"],  # the provider credentials live in the user data dir
    "XDG_CONFIG_HOME": str(OPENCODE / "config"),
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "LANG": "C.UTF-8",
    "PATH": "/usr/bin:" + str(Path(os.environ.get("OPENCODE_BIN", "/usr/bin/opencode")).parent),
}


def load(paths: list[str]) -> list[dict]:
    return [c for p in paths for c in json.loads(Path(p).read_text())]


def prompt(case: dict, arm: str) -> str:
    files = "".join(f["overview"][arm] + "\n" for f in case["files"])
    return PROMPT.format(ranges=MAX_RANGES, lines=MAX_LINES, issue=case["query"], files=files)


def configure() -> None:
    no_tools = {"permission": {"*": "deny"}, "tools": {"*": False}}
    config = {"mcp": {}, "plugin": [], "instructions": [], **no_tools}
    config["agent"] = {"nav": {"mode": "primary", "prompt": SYSTEM, **no_tools}}
    path = OPENCODE / "config/opencode/opencode.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=1) + "\n")


def overview_units(case: dict, arm: str) -> int:
    return sum(units(f["overview"][arm]) for f in case["files"])


def limited(record: dict) -> bool:
    return any("Rate limit" in e.get("message", "") for e in record.get("errors", []))


def call(text: str, out: Path) -> None:
    command = [
        "timeout", str(TIMEOUT_S), "opencode", "run", "--standalone",
        "--model", MODEL, "--agent", "nav", "--format", "json",  # message on stdin
    ]  # fmt: skip
    tries = 0  # real failures retry once; rate limits (not evidence) wait and retry
    for attempt in range(12):
        began = time.time()
        done = subprocess.run(
            command, capture_output=True, text=True, env=ENV,
            cwd=OPENCODE / "home", input=text,
        )  # fmt: skip
        events = []
        for line in done.stdout.splitlines():
            with contextlib.suppress(ValueError):
                events.append(json.loads(line))
        parts = [e.get("part", {}) for e in events]
        steps = [p for p in parts if p.get("type") == "step-finish"]
        errors = [e["error"] for e in events if e.get("type") == "error"]
        record = {
            "exit": done.returncode,
            "seconds": round(time.time() - began, 2),
            "result": "".join(p.get("text", "") for p in parts if p.get("type") == "text"),
            "tools": [
                [p.get("tool"), p.get("state", {}).get("status"), p.get("state", {}).get("input")]
                for p in parts
                if p.get("type") == "tool"
            ],
            "tokens": [p["tokens"] for p in steps],
            "total_cost_usd": sum(p.get("cost", 0) for p in steps),
            "is_error": bool(errors) or not events,
            "errors": errors,
        }
        if not events:
            record["stdout"], record["stderr"] = done.stdout[-2000:], done.stderr[-2000:]
        if done.returncode == 0 and not record["is_error"]:
            break
        record["attempt"] = attempt
        if limited(record):
            time.sleep(min(300, 60 * (attempt + 1)))
            continue
        tries += 1
        if tries == 2:
            break
    out.write_text(json.dumps(record, indent=1) + "\n")


def run(rep: str, arms: list[str], workers: int, cases: list[dict]) -> None:
    configure()
    jobs = [(c, a) for c in cases for a in arms]
    random.Random(f"{SEED}:{rep}").shuffle(jobs)
    todo = []
    for case, arm in jobs:
        out = DATA / "nav-runs" / rep / arm / f"{case['task']}.json"
        if not out.exists() or limited(json.loads(out.read_text())):
            out.parent.mkdir(parents=True, exist_ok=True)
            todo.append((prompt(case, arm), out))
    print(f"{len(todo)} calls", file=sys.stderr)
    with ThreadPoolExecutor(workers) as pool:
        for n, _ in enumerate(pool.map(lambda j: call(*j), todo), 1):
            if n % 25 == 0:
                print(f"{n}/{len(todo)}", file=sys.stderr)


def parse(record: dict) -> list[dict] | None:
    text = record.get("result") or ""
    found = re.search(r"\{.*\}", text, re.S)
    try:
        ranges = json.loads(found.group(0))["ranges"] if found else None
    except (ValueError, KeyError, TypeError):
        return None
    return ranges if isinstance(ranges, list) else None


def grade(case: dict, arm: str, ranges: list[dict] | None) -> dict:
    files = {f["path"]: f for f in case["files"]}
    gold = {(s["file"], n) for s in case["spans"] for n in s["lines"]}
    paid = overview_units(case, arm)
    read: list[tuple[str, int, int]] = []  # (path, line, cumulative read units)
    seen: set[tuple[str, int]] = set()
    spent = 0
    touched = set()
    for r in (ranges or [])[:MAX_RANGES]:
        try:
            path, start, end = r["path"], int(r["start"]), int(r["end"])
        except (KeyError, TypeError, ValueError):
            continue
        f = files.get(path)
        if f is None:
            continue
        start, end = max(1, start), min(f["lines"], end)
        if start > end:
            continue
        touched.add(path)
        spent += units(f"### {path}:{start}-{end}\n")
        for n in range(start, end + 1):
            if len(read) >= MAX_LINES:
                break
            if (path, n) not in seen:
                seen.add((path, n))
                spent += f["units"][n - 1]
                read.append((path, n, spent))
    got = lambda cap: {(p, n) for p, n, s in read if s <= cap}  # noqa: E731
    recall = lambda lines: round(len(gold & lines) / len(gold), 6) if gold else None  # noqa: E731
    gold_files = {s["file"] for s in case["spans"]}
    out = {
        "task": case["task"],
        "bench": case["bench"],
        "split": case["split"],
        "corpus": case["corpus"],
        "arm": arm,
        "valid": ranges is not None,
        "overview_units": paid,
        "read_lines": len(read),
        "read_units": read[-1][2] if read else 0,
        "recall": recall(got(1 << 40)),
        "gold_file_hit": round(len(gold_files & touched) / len(gold_files), 6)
        if gold_files
        else None,
        "precision": round(len(gold & got(1 << 40)) / len(read), 6) if read else 0.0,
    }
    for t in BUDGETS:
        out[f"recall@{t}"] = recall(got(t - paid) if paid <= t else set())
    return out


def main() -> None:
    cmd = sys.argv[1]
    if cmd == "sizes":
        for case in load(sys.argv[2:]):
            sizes = {a: overview_units(case, a) for a in case["files"][0]["overview"]}
            print(case["task"], sizes)
    elif cmd == "run":
        rep, arms, workers = sys.argv[2], sys.argv[3].split(","), int(sys.argv[4])
        run(rep, arms, workers, load(sys.argv[5:]))
    elif cmd == "grade":
        rows = []
        for case in load(sys.argv[3:]):
            for rep_dir in sorted((DATA / "nav-runs").iterdir()):
                for arm_dir in sorted(rep_dir.iterdir()):
                    f = arm_dir / f"{case['task']}.json"
                    if f.exists():
                        record = json.loads(f.read_text())
                        # a tool that ran saw more than the overview: the call is invalid
                        ran = [t for t in record.get("tools", []) if t[1] == "completed"]
                        row = grade(case, arm_dir.name, None if ran else parse(record))
                        row["tool_ran"] = bool(ran)
                        row["rep"] = rep_dir.name
                        row["cost_usd"] = record.get("total_cost_usd")
                        row["failed"] = record.get("exit") != 0 or bool(record.get("is_error"))
                        rows.append(row)
        Path(sys.argv[2]).write_text(json.dumps(rows, indent=1) + "\n")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
