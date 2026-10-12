#!/usr/bin/env python3
"""Candidate pools for a follow-up navigation eval (follow-up.md § Task 3). Deterministic;
uses only SWE-bench Verified metadata and the gold patch, never OXIDE output or outcomes.

Eligible: SWE-bench Verified, minus (1) every instance id named anywhere under docs/ (already
used for OXIDE design, tuning or evaluation, including the 28 agent-nav tasks, which are now
diagnostic), (2) matplotlib (images do not unpack under rootless podman here), (3) repos where
OXIDE was iterated (DecisionBench dev: sympy, sphinx). pylint, seaborn and flask were never
used by OXIDE (fully fresh repositories).

Stratum B (structurally demanding), preregistered criteria over the gold patch:
  B1  edits >= 2 non-test source files, or
  B2  edits >= 2 distinct enclosing symbols (hunk-header or edited def/class names) with
      >= 10 changed lines, or
  B3  changes a def signature (a removed and an added `def NAME(` line for the same NAME),
      so callers elsewhere must stay consistent.
Stratum A (representative): a seeded random order of all eligible tasks.

  strata.py   (needs `datasets`; run with the agent-nav venv, HF_DATASETS_OFFLINE=1)
"""
import json
import random
import re
import subprocess
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEV_REPOS = {"sympy/sympy", "sphinx-doc/sphinx"}
UNGRADABLE = {"matplotlib/matplotlib"}
FRESH_REPOS = {"pylint-dev/pylint", "mwaskom/seaborn", "pallets/flask"}
TEST = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|conftest\.py$")
DEF = r"(?:async\s+)?def\s+(\w+)\("


def used_ids():
    out = subprocess.run(["git", "-C", str(REPO), "grep", "-ohE", r"[A-Za-z0-9_-]+__[A-Za-z0-9_-]+-[0-9]+",
                          "--", "docs"], capture_output=True, text=True, check=True).stdout
    return set(out.split())


def features(patch):
    src = [f for f in re.findall(r"^\+\+\+ b/(\S+)", patch, re.M) if f.endswith(".py") and not TEST.search(f)]
    symbols = set(re.findall(r"^@@ .*@@\s*(?:async\s+)?(?:def|class)\s+(\w+)", patch, re.M))
    symbols |= set(re.findall(r"^[+-]\s*(?:async\s+)?(?:def|class)\s+(\w+)", patch, re.M))
    changed = sum(1 for ln in patch.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---")))
    b1 = len(src) >= 2
    b2 = len(symbols) >= 2 and changed >= 10
    b3 = bool(set(re.findall(r"^-\s*" + DEF, patch, re.M)) & set(re.findall(r"^\+\s*" + DEF, patch, re.M)))
    return {"src_files": len(src), "symbols": len(symbols), "changed_lines": changed,
            "B1": b1, "B2": b2, "B3": b3, "structural": b1 or b2 or b3}


def describe(rs):
    return {"n": len(rs), "repos": dict(Counter(r["repo"] for r in rs).most_common()),
            "difficulty": dict(Counter(r["difficulty"] for r in rs).most_common())}


def main():
    from datasets import load_dataset
    used = used_ids()
    rows = [{"instance_id": r["instance_id"], "repo": r["repo"], "difficulty": r["difficulty"],
             "used_before": r["instance_id"] in used, **features(r["patch"])}
            for r in load_dataset("SWE-bench/SWE-bench_Verified", split="test")]
    nav = {t["instance_id"] for t in json.loads((REPO / "docs/agent-navigation-eval/tasks.json").read_text())["tasks"]}
    eligible = sorted((r for r in rows if not r["used_before"] and r["repo"] not in UNGRADABLE | DEV_REPOS),
                      key=lambda r: r["instance_id"])
    b = [r for r in eligible if r["structural"]]
    rng = random.Random("oxide-agentnav-followup/v1")
    rng.shuffle(b)
    a = list(eligible)
    rng.shuffle(a)
    print(json.dumps({
        "verified": len(rows), "named_in_docs": sum(r["used_before"] for r in rows),
        "agentnav_tasks_all_named_in_docs": all(r["used_before"] for r in rows if r["instance_id"] in nav),
        "agentnav_profile": {**describe([r for r in rows if r["instance_id"] in nav]),
                             "structural": sum(r["structural"] for r in rows if r["instance_id"] in nav)},
        "eligible": {**describe(eligible), "structural_share": round(len(b) / len(eligible), 3)},
        "stratum_B": {**describe(b), "B1": sum(r["B1"] for r in b), "B2": sum(r["B2"] for r in b),
                      "B3": sum(r["B3"] for r in b), "fresh_repo_tasks": sum(r["repo"] in FRESH_REPOS for r in b)},
        "feasibility_sample": {"B": [r["instance_id"] for r in b[:6]],
                               "A": [r["instance_id"] for r in a if not r["structural"]][:4]},
        "pool_order": {"B": [r["instance_id"] for r in b], "A": [r["instance_id"] for r in a]},
    }, indent=1))


if __name__ == "__main__":
    main()
