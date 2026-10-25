"""AGC v3 agent trial (preregistration.md): AGC v2's harness, new tasks, arms and store.

    trial.py check                 every truth/neutral key exists; checkouts digested
    trial.py prompt TASK ARM       print one run's system prompt and message
    trial.py run REP WORKERS       every task x arm once, in a seeded order, skipping done
    trial.py dev                   the unscored dev task, every arm (harness check)
    trial.py grade                 score every finished run -> results/runs.json
    trial.py verify                the checkouts still match the digests from `check`

The harness, MCP server (`../../agc-v2/scripts/server.py`, unchanged) and grading are AGC
v2's, frozen at 5cf699d. This file loads that module and rebinds its configuration: the
data and store paths, the seed, the arms and their system prompts. Arms differ only in
the system prompt and whether the server is attached; the message is the same.
"""

from __future__ import annotations

import importlib.util
import json
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

EVAL = Path.home() / "Projects/oxide-eval-data"
DIRECT = {"execute": False, "server": "direct"}
ARMS = {
    "B": {"execute": False, "server": None},  # capable grep/read
    "D": DIRECT,  # + oxide_context, AGC v2's direct shape and description
    "G": DIRECT,  # + guidance by question type
    "F": DIRECT,  # diagnostic: G, and the first call must be oxide_context
}
GUIDANCE = (
    "This checkout also has OXIDE's `oxide_context` tool, which answers relationship "
    "questions from a code index. Use it by question type:\n"
    "- Who calls X, or what is affected if X changes: call oxide_context(target=X, "
    "relation='callers') first, with depth=2 when callers of callers matter. It lists the "
    "call sites the index resolved, each with its source line, then the text matches of "
    "the name that no edge explains. Read only to confirm a listed site or to settle an "
    "unexplained match; do not grep for the name again.\n"
    "- What X calls: relation='callees'.\n"
    "- Getting oriented in an unfamiliar file or class: oxide_context(target=<file or "
    "class>) for an outline with line ranges.\n"
    "- Where a single name is defined: grep and read directly."
)
FORCE = (
    "Your first tool call must be oxide_context on the symbol the question names, with "
    "the relation the question needs. After that, work as you see fit."
)
PROMPTS = {
    "B": v2.SYSTEM,
    "D": v2.SYSTEM,
    "G": f"{v2.SYSTEM}\n\n{GUIDANCE}",
    "F": f"{v2.SYSTEM}\n\n{GUIDANCE}\n\n{FORCE}",
}
REPOS = ("xarray", "seaborn")


def load() -> dict[str, Any]:
    return json.loads((HERE.parent / "tasks.json").read_text())


def system(arm: str) -> str:
    return PROMPTS[arm]


_ready = v2.ready
SETTLE_S = 2.0


def ready(url: str, env: dict[str, str], cwd: Path, want_mcp: bool) -> bool:
    """v2's check, then a settle: a dev run showed `connected` before the tool was
    listed to the model (README § Harness)."""
    ok = _ready(url, env, cwd, want_mcp)
    if ok and want_mcp:
        time.sleep(SETTLE_S)
    return bool(ok)


# v2's functions read these module globals at call time
v2.load, v2.system, v2.ARMS, v2.SEED, v2.ready = load, system, ARMS, 2026102503, ready
v2.DATA, v2.RESULTS = EVAL / "oxide-agc3-eval", HERE.parent / "results"
v2.STORE = EVAL / "oxide-agc3-eval/data"


def cmd_check() -> None:
    from agc import Agc

    for t in v2.by_id().values():
        agc = Agc(v2.REPOS / t["repo"], v2.STORE)
        missing = [i for i in t["truth"] + t["neutral"] if v2.v1.key(i) not in agc.info]
        assert not missing, (t["id"], missing)
    sums = {r: v2.v1.digest(v2.REPOS / r) for r in REPOS}
    v2.DATA.mkdir(parents=True, exist_ok=True)
    (v2.DATA / "digests.json").write_text(json.dumps(sums, indent=1) + "\n")
    print("keys ok;", sums)


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "check":
        cmd_check()
    elif command == "prompt":
        task = v2.by_id()[sys.argv[2]]
        print(system(sys.argv[3]), "\n---\n", v2.v1.MESSAGE.format(**task))
    elif command == "run":
        v2.cmd_run(f"r{sys.argv[2]}", int(sys.argv[3]))
    elif command == "dev":
        v2.cmd_run("dev", 2, [load()["dev"]])
    elif command in ("grade", "verify"):
        getattr(v2, f"cmd_{command}")()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
