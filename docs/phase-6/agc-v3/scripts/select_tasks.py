"""AGC v3 task selection: fixed structural rules, run before any agent run.

    python select_tasks.py     # writes results/selection.json (seeded candidate order)

Library code only: files under the package directory, not tests. Two task types, both
picked from text and names alone, never from OXIDE edges or any agent outcome:

- dispatch: a method (no overload suffix, no dunder) whose short name is defined at
  least 3 times in library code and called through `self.`, `cls.` or `super().` at
  4-20 library sites, so only the class hierarchy tells which calls reach the target.
  (A first rule counted every `name(` site; most of those receivers are undecidable
  without data flow, so their truth could not be written. Revised before any run.)
- impact: a module-level function with a name defined once in library code and 3-8
  library call sites; the question asks for callers up to two hops away.

Each pool is shuffled with a fixed seed. Truth is then written by hand down that order
(preregistration § Tasks): one target per short name and class, skipping a candidate only
if its hand-written truth has fewer than 2 items, until 2 per type per repository.
"""

import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "agc/scripts"))
from agc import Agc  # noqa: E402

EVAL = Path.home() / "Projects/oxide-eval-data"
REPOS = EVAL / "oxide-structural-trial/repos"
STORE = EVAL / "oxide-agc3-eval/data"
SEED = 2026102503
RULES = {"dispatch": (3, 4, 20), "impact": (1, 3, 8)}  # (definitions, min/max sites)


def library(path: str, repo: str) -> bool:
    name = Path(path).name
    return (path.startswith(repo + "/") and "tests" not in path.split("/")
            and not name.startswith("test_") and name != "conftest.py")  # fmt: skip


def pools(repo: str) -> dict[str, list[dict]]:
    agc = Agc(REPOS / repo, STORE)
    code = [k for k in agc.symbols if not agc.info[k][8] and library(agc.info[k][4], repo)
            and agc.info[k][2] in ("function", "method")]  # fmt: skip
    defined = Counter(agc.info[k][3] for k in code)
    texts = [(REPOS / repo / p).read_text("utf-8", "replace") for p in agc.files
             if p.endswith(".py") and library(p, repo)]  # fmt: skip
    out: dict[str, list[dict]] = {t: [] for t in RULES}
    for k in code:
        name, kind, qual = agc.info[k][3], agc.info[k][2], Agc.qual(k)
        if name.startswith("__") or "~" in k:
            continue
        kind_ok = {"dispatch": kind == "method", "impact": kind == "function" and "." not in qual}
        call = re.compile(rf"(?<!def )(?<![\w.]){re.escape(name)}\(|\.{re.escape(name)}\(")
        own = re.compile(rf"\b(?:self|cls|super\(\))\.{re.escape(name)}\(")
        counts = {"dispatch": sum(len(own.findall(t)) for t in texts),
                  "impact": sum(len(call.findall(t)) for t in texts)}  # fmt: skip
        for t, (defs, low, high) in RULES.items():
            sites = counts[t]
            many = defined[name] >= defs if t == "dispatch" else defined[name] == defs
            if kind_ok[t] and many and low <= sites <= high:
                out[t].append({"repo": repo, "type": t, "key": k, "name": name,
                               "definitions": defined[name], "call_sites": sites})  # fmt: skip
    for t in out:
        out[t].sort(key=lambda c: c["key"])
        random.Random(f"{SEED}-{repo}-{t}").shuffle(out[t])
    return out


def main() -> None:
    picked = {f"{r}/{t}": pool for r in ("xarray", "seaborn") for t, pool in pools(r).items()}
    (HERE.parent / "results/selection.json").write_text(json.dumps(picked, indent=1) + "\n")
    for name, pool in picked.items():
        print(name, len(pool), [(c["key"].split(":", 1)[1], c["call_sites"]) for c in pool[:8]])


if __name__ == "__main__":
    main()
