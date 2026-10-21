"""Agent stage (preregistration.md § Questions, § Agent): comprehension and navigation.

    agent.py prompt CASE_ID ARM           print one prompt (no model call)
    agent.py run REP WORKERS              one call per case x arm, skipping finished ones
    agent.py grade                        score every finished call -> results/agent.json

The agent is OpenCode with a fixed model and a custom agent that has no tools, an
isolated config (no MCP, plugins or instructions), an empty working directory and one
turn, as in outline-comparison/scripts/nav.py. Prompts differ only in the output text.
The format is never named, as an MCP client would not name it either.
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
from typing import Any

DATA = Path.home() / "Projects/oxide-eval-data/oxide-toon-eval"
RESULTS = Path(__file__).resolve().parents[1] / "results"
MODEL = "openai/gpt-6-luna"
SEED = 20261021
TIMEOUT_S = 600
SYSTEM = "You answer questions about a code-search tool's output. Answer with JSON only."
QUIZ = """Below is the output a code-search tool returned. Answer every question using only \
that output.

<output>
{text}
</output>

Questions:
{questions}

Reply with only JSON: {{"answers": {{"q1": <answer>, ...}}}}. Give numbers as JSON numbers, \
true/false as JSON booleans, null as null, and every list answer as a JSON array (empty if \
there is none).
"""
NAV = """An issue was reported in a code repository. Below is the output a code-search tool \
returned for the issue text. You cannot open any file. Choose at most 3 repository file \
paths, most important first, that a developer should open first to change the code for \
this issue.

<issue>
{issue}
</issue>

<output>
{text}
</output>

Reply with only JSON: {{"paths": ["<path>", ...]}}
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


def cases() -> list[dict[str, Any]]:
    return json.loads((DATA / "cases.json").read_text())


def prompt(case: dict[str, Any], arm: str) -> str:
    text = case["texts"][arm]
    if case["kind"] == "nav":
        return NAV.format(issue=case["issue"], text=text)
    questions = "\n".join(f"q{n}: {q['text']}" for n, q in enumerate(case["questions"], 1))
    return QUIZ.format(text=text, questions=questions)


def configure() -> None:
    no_tools = {"permission": {"*": "deny"}, "tools": {"*": False}}
    config = {"mcp": {}, "plugin": [], "instructions": [], **no_tools}
    config["agent"] = {"quiz": {"mode": "primary", "prompt": SYSTEM, **no_tools}}
    path = OPENCODE / "config/opencode/opencode.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=1) + "\n")
    (OPENCODE / "home").mkdir(exist_ok=True)


def limited(record: dict[str, Any]) -> bool:
    return any("Rate limit" in e.get("message", "") for e in record.get("errors", []))


def call(text: str, out: Path) -> None:
    command = [
        "timeout", str(TIMEOUT_S), "opencode", "run", "--standalone",
        "--model", MODEL, "--agent", "quiz", "--format", "json",  # message on stdin
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
                [p.get("tool"), p.get("state", {}).get("status")]
                for p in parts
                if p.get("type") == "tool"
            ],  # fmt: skip
            "tokens": [p["tokens"] for p in steps],
            "total_cost_usd": sum(p.get("cost", 0) for p in steps),
            "is_error": bool(errors) or not events,
            "errors": errors,
        }
        if not events:
            record["stdout"], record["stderr"] = done.stdout[-2000:], done.stderr[-2000:]
        if done.returncode == 0 and not record["is_error"]:
            # opencode 2.0's JSON stream has no step-finish; usage is in the session export.
            session = events[0].get("sessionID")
            exported = subprocess.run(
                ["opencode", "session", "export", str(session)], capture_output=True,
                text=True, env=ENV, cwd=OPENCODE / "home",
            )  # fmt: skip
            with contextlib.suppress(ValueError):
                messages = json.loads(exported.stdout)["messages"]
                usage = [m for m in messages if m.get("type") == "assistant"]
                record["tokens"] = [m.get("tokens") for m in usage]
                record["total_cost_usd"] = sum(m.get("cost") or 0 for m in usage)
            record["session"] = session
            break
        record["attempt"] = attempt
        if limited(record):
            time.sleep(min(300, 60 * (attempt + 1)))
            continue
        tries += 1
        if tries == 2:
            break
    out.write_text(json.dumps(record, indent=1) + "\n")


def run(rep: str, workers: int) -> None:
    configure()
    jobs = [(c, a) for c in cases() for a in c["arms"]]
    random.Random(f"{SEED}:{rep}").shuffle(jobs)
    todo = []
    for case, arm in jobs:
        out = DATA / "runs" / rep / arm / f"{case['id'].replace(':', '~')}.json"
        if not out.exists() or limited(json.loads(out.read_text())):
            out.parent.mkdir(parents=True, exist_ok=True)
            todo.append((prompt(case, arm), out))
    print(f"{len(todo)} calls", file=sys.stderr)
    with ThreadPoolExecutor(workers) as pool:
        for n, _ in enumerate(pool.map(lambda job: call(*job), todo), 1):
            if n % 25 == 0:
                print(f"{n}/{len(todo)}", file=sys.stderr)


# --- grading ---


def reply(record: dict[str, Any]) -> dict[str, Any] | None:
    found = re.search(r"\{.*\}", record.get("result") or "", re.S)
    try:
        value = json.loads(found.group(0)) if found else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def scalar(got: Any, gold: Any) -> bool:
    if gold is None:
        return got is None or (isinstance(got, str) and got.strip().lower() == "null")
    if isinstance(gold, bool):
        return got is gold or (isinstance(got, str) and got.strip().lower() == str(gold).lower())
    if isinstance(gold, int):
        with contextlib.suppress(TypeError, ValueError):
            return not isinstance(got, bool) and float(got) == gold
        return False
    return isinstance(got, str) and got.strip() == gold.strip()


def correct(question: dict[str, Any], got: Any) -> bool:
    gold, grade = question["gold"], question["grade"]
    if grade == "exact" and isinstance(gold, str):
        return isinstance(got, str) and got.rstrip().strip("\n") == gold.rstrip()
    if grade == "set":
        if not isinstance(got, list) or not all(isinstance(g, str) for g in got):
            return False
        return {g.strip() for g in got} == {g.strip() for g in gold}
    if grade == "pair":
        return isinstance(got, list) and len(got) == len(gold) and all(map(scalar, got, gold))
    return scalar(got, gold)


def prompt_tokens(record: dict[str, Any]) -> int | None:
    steps = record.get("tokens") or []
    if not steps:
        return None
    t = steps[0]
    cache = t.get("cache") or {}
    return int(t.get("input", 0) + cache.get("read", 0) + cache.get("write", 0))


def grade() -> None:
    rows = []
    for case in cases():
        for rep_dir in sorted((DATA / "runs").iterdir()):
            for arm in case["arms"]:
                path = rep_dir / arm / f"{case['id'].replace(':', '~')}.json"
                if not path.exists():
                    continue
                record = json.loads(path.read_text())
                ran = [t for t in record.get("tools", []) if t[1] == "completed"]
                answer = None if ran else reply(record)
                row: dict[str, Any] = {
                    "id": case["id"], "kind": case["kind"], "corpus": case["corpus"],
                    "arm": arm, "rep": rep_dir.name, "seconds": record.get("seconds"),
                    "prompt_tokens": prompt_tokens(record),
                    "cost_usd": record.get("total_cost_usd"),
                    "failed": record.get("exit") != 0 or bool(record.get("is_error")),
                    "tool_ran": bool(ran),
                }  # fmt: skip
                if case["kind"] == "nav":
                    paths = answer.get("paths") if answer else None
                    valid = isinstance(paths, list)
                    chosen = (
                        {str(p).strip().removeprefix("./") for p in paths[:3]} if valid else set()
                    )
                    gold = set(case["gold_files"])
                    row |= {
                        "valid": valid,
                        "hit": float(bool(chosen & gold)),
                        "file_recall": len(chosen & gold) / len(gold),
                    }
                else:
                    answers = answer.get("answers") if answer else None
                    valid = isinstance(answers, dict)
                    row["valid"] = valid
                    row["correct"] = [
                        valid and correct(q, answers.get(f"q{n}"))
                        for n, q in enumerate(case["questions"], 1)
                    ]
                    row["answers"] = answers
                rows.append(row)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "agent.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    print(len(rows), "graded calls")


def main() -> None:
    command = sys.argv[1]
    if command == "prompt":
        case = next(c for c in cases() if c["id"] == sys.argv[2])
        print(prompt(case, sys.argv[3]))
    elif command == "run":
        run(sys.argv[2], int(sys.argv[3]))
    elif command == "grade":
        grade()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
