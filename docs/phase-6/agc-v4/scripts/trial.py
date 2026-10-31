"""AGC v4 agent trial (preregistration.md): one tool vs two, natural vs forced use.

    trial.py check                 every truth/neutral key exists; checkouts digested
    trial.py prompt TASK ARM       print one run's system prompt and message
    trial.py run REP WORKERS       every task x arm once, in a seeded order, skipping done
    trial.py dev                   the unscored dev task, every arm (harness check)
    trial.py grade                 score every finished run -> results/runs.json
    trial.py verify                the checkouts still match the digests from `check`

AGC v2's harness (frozen at 5cf699d) with v3's 2 s settle after MCP `connected`, this
study's server (`server.py`, shapes single/split) and a grader that also reads TypeScript
citations: v1's answer pattern and `Agc.resolve` know only `.py`. Scoring is v1's.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "agc2_trial", HERE.parents[1] / "agc-v2/scripts/trial.py"
)
assert _spec and _spec.loader
v2 = importlib.util.module_from_spec(_spec)
sys.modules["agc2_trial"] = v2
_spec.loader.exec_module(v2)
v1 = v2.v1

EVAL = Path.home() / "Projects/oxide-eval-data"
ARMS = {
    "B": {"execute": False, "server": None},  # capable grep/read
    "S": {"execute": False, "server": "single"},  # oxide_search with graph and outline modes
    "T": {"execute": False, "server": "split"},  # production oxide_search + oxide_context
    "F": {"execute": False, "server": "single"},  # diagnostic: S, first call forced
}
FORCE = "Your first tool call must be oxide_search. After that, work as you see fit."
REPOS = ("pytest", "flask", "vue-core")
SETTLE_S = 2.0
ANSWER = re.compile(r"([\w./@+\-\[\]]+\.(?:py|ts|tsx|js)):(\d+)")


def load() -> dict[str, Any]:
    return json.loads((HERE.parent / "tasks.json").read_text())


def system(arm: str) -> str:
    return f"{v2.SYSTEM}\n\n{FORCE}" if arm == "F" else v2.SYSTEM


def configure(out: Path, arm: str, repo: str) -> dict[str, str]:
    """v2's isolated OpenCode config, with this study's server."""
    permission = dict.fromkeys(v1.DENY, "deny") | dict.fromkeys(
        ("read", "grep", "glob", "list"), "allow"
    )
    permission["execute"] = "deny"
    servers = {}
    if shape := ARMS[arm]["server"]:
        permission["agc_*"] = "allow"
        command = [v1.PYTHON, str(HERE / "server.py"), "--repo", str(v2.REPOS / repo),
                   "--data-dir", str(v2.STORE), "--shape", shape,
                   "--log", str(out / "mcp.jsonl")]  # fmt: skip
        servers["agc"] = {"type": "local", "command": command, "codemode": False}
    agent = {"mode": "primary", "prompt": system(arm), "permission": permission}
    config = {"mcp": {"servers": servers}, "plugin": [], "instructions": [],
              "permission": permission, "agent": {"trial": agent}}  # fmt: skip
    path = out / "config/opencode/opencode.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=1) + "\n")
    return {
        "HOME": os.environ["HOME"],
        "XDG_CONFIG_HOME": str(out / "config"),
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_PASSWORD": secrets.token_hex(16),
        "LANG": "C.UTF-8",
        "PATH": "/usr/bin:" + str(Path.home() / ".bun/bin"),
    }


_ready = v2.ready


def ready(url: str, env: dict[str, str], cwd: Path, want_mcp: bool) -> bool:
    """v2's check, then v3's settle (a dev run once saw `connected` before the tools)."""
    ok = _ready(url, env, cwd, want_mcp)
    if ok and want_mcp:
        time.sleep(SETTLE_S)
    return bool(ok)


def answers(text: str) -> list[tuple[str, int]]:
    tail = text.rsplit("ANSWER:", 1)[1] if "ANSWER:" in text else ""
    return sorted({(p.removeprefix("./"), int(n)) for p, n in ANSWER.findall(tail)})


def owner(agc: Any, path: str, line: int) -> str:
    """The innermost symbol whose span holds `path:line`, else the file (v1's rule)."""
    inside = [k for k in agc.symbols if agc.info[k][4] == path
              and agc.span(k)[0] <= line <= agc.span(k)[1]]  # fmt: skip
    if inside:
        return str(min(inside, key=lambda k: agc.info[k][6] - agc.info[k][5]))
    return f"file:{path}" if path in agc.files else f"?:{path}"


def cmd_grade() -> None:
    from agc import Agc

    known = v2.by_id()
    agcs = {r: Agc(v2.REPOS / r, v2.STORE) for r in {t["repo"] for t in known.values()}}
    rows = []
    for path in sorted((v2.DATA / "runs").glob("*/*/*/record.json")):
        record = json.loads(path.read_text())
        task = known[record["task"]]
        agc = agcs[task["repo"]]
        found = answers(record["result"])
        claims = {owner(agc, p, n) for p, n in found}
        kinds = {k: agc.info[k][2] for k in claims if k in agc.info}
        row = {k: record.get(k) for k in ("task", "arm", "rep", "seconds", "exit")}
        failed = bool(record["is_error"]) or not found
        row |= {"type": task["type"], "lang": task["lang"], "answers": len(found),
                "technical_failure": failed}  # fmt: skip
        row |= v2.usage(record, v2.REPOS / task["repo"]) | v1.score(claims, task, kinds)
        row["success"] = not failed and row["f1"] >= 0.8 and row["false_claims"] == 0
        modes = [m.get("mode", "") for m in record["mcp"]]
        row["modes"] = {m: modes.count(m) for m in sorted(set(modes))}
        row["first_tool"] = record["tools"][0][0] if record["tools"] else None
        rows.append(row)
    v2.RESULTS.mkdir(exist_ok=True)
    (v2.RESULTS / "runs.json").write_text(json.dumps(rows, indent=1) + "\n")
    print(len(rows), "runs graded")


def cmd_check() -> None:
    from agc import Agc

    for t in v2.by_id().values():
        agc = Agc(v2.REPOS / t["repo"], v2.STORE)
        missing = [i for i in t["truth"] + t["neutral"] if v1.key(i) not in agc.info]
        assert not missing, (t["id"], missing)
    sums = {r: v1.digest(v2.REPOS / r) for r in REPOS}
    v2.DATA.mkdir(parents=True, exist_ok=True)
    (v2.DATA / "digests.json").write_text(json.dumps(sums, indent=1) + "\n")
    print("keys ok;", sums)


# v2's functions read these module globals at call time
v2.load, v2.system, v2.ARMS, v2.SEED = load, system, ARMS, 2026102506
v2.configure, v2.ready = configure, ready
v2.DATA, v2.RESULTS = EVAL / "oxide-agc4-eval", HERE.parent / "results"
v2.STORE = EVAL / "oxide-agc4-eval/data"


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "check":
        cmd_check()
    elif command == "prompt":
        task = v2.by_id()[sys.argv[2]]
        print(system(sys.argv[3]), "\n---\n", v1.MESSAGE.format(**task))
    elif command == "run":
        v2.cmd_run(f"r{sys.argv[2]}", int(sys.argv[3]))
    elif command == "dev":
        v2.cmd_run("dev", 2, [load()["dev"]])
    elif command == "grade":
        cmd_grade()
    elif command == "verify":
        v2.cmd_verify()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
