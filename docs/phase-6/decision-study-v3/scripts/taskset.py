"""Decision Study v3 evaluation set (preregistration.md § Evaluation set).

    uv run --with pyarrow python docs/phase-6/decision-study-v3/scripts/taskset.py PARQUET

Builds the sealed confirmation set from ContextBench ``full.parquet`` (the same pinned
file as the baseline) and writes, next to this directory, ``tasks.json`` (the
``contextbench.py`` task format, split ``v3``), ``gold-audit.json`` (the baseline's audit
rule, ``contextbench.classify``, per span) and ``separation.json``. It fetches pinned
corpora (git) and runs no OXIDE code: nothing is indexed, queried or scored.

Rule, in order: every ``python`` row (1) whose repository is not one of the baseline's
(``contextbench.ELIGIBLE``: all of Decision Studies v1/v2, the reliability study and every
Python-engine benchmark); (2) whose instance ids are named in no prior artifact (``SCAN``);
(3) per repository in ``sha256("ds3:" + instance_id)`` order, accepted while the
repository has fewer than ``PER_REPO`` accepted tasks, if its corpus has at most
``MAX_PY_FILES`` ``*.py`` files and at least one gold span is verified with a code line.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "tests" / "python"))

import contextbench as cb  # noqa: E402

SPLIT = "v3"
PER_REPO = 8
MAX_PY_FILES = 3000  # cost rule; a 1,550-file corpus indexed in 4.8 s (README § Feasibility)
SEED = "ds3:"
SCAN = {
    "roots": ["~/Projects/oxide-eval-data", "docs", "tests", "scripts", "eval-agent"],
    "pruned": [
        "wt",
        "wt-parent",
        "cb-wt",
        "cb-repos",
        "repos",
        "corpora",
        "git",
        "stores",
        ".oxide",
        "oxide-decision-study-v3",
        "decision-study-v3",
    ],
    "files": ["*.json", "*.jsonl", "*.md", "*.txt", "*.csv", "*.log", "*.py"],
}


def prior_ids(ids: set[str]) -> dict[str, int]:
    """Every candidate id named in a prior artifact, with its number of mentions.
    Substring matching: a short id inside a longer one also counts (conservative)."""
    patterns = HERE.parent / ".ds3-scan-ids"
    patterns.write_text("\n".join(sorted(ids)) + "\n")
    roots = [str(Path(r).expanduser() if r[0] == "~" else REPO / r) for r in SCAN["roots"]]
    prune = [a for n in SCAN["pruned"] for a in ("-name", n, "-o")][:-1]
    names = [a for n in SCAN["files"] for a in ("-name", n, "-o")][:-1]
    find = ["find", *roots, "(", *prune, ")", "-prune", "-o", "-type", "f", "(", *names, ")"]
    listed = subprocess.run([*find, "-print0"], capture_output=True, check=False).stdout
    found = subprocess.run(
        ["xargs", "-0", "grep", "-oFh", "-f", str(patterns)], input=listed, capture_output=True
    ).stdout.decode()
    patterns.unlink()
    return dict(Counter(found.split()))


def rank(row: dict[str, Any]) -> str:
    return hashlib.sha256((SEED + row["instance_id"]).encode()).hexdigest()


def main(parquet: Path) -> None:
    cb.MAX_PY_FILES = 1_000_000  # counted here instead, so an over-cap corpus is skipped
    rows = [r for r in cb._parquet(parquet) if r["language"] == "python"]
    baseline = json.loads(cb.TASKS.read_text())
    candidates = [r for r in rows if r["repo"] not in cb.ELIGIBLE]
    mentioned = prior_ids(
        {x for r in candidates for x in (r["instance_id"], r["original_inst_id"])}
    )
    fresh = [
        r for r in candidates if not {r["instance_id"], r["original_inst_id"]} & set(mentioned)
    ]
    tasks: list[dict[str, Any]] = []
    corpora: dict[str, dict[str, str]] = {}
    audit: dict[str, list[dict[str, Any]]] = {}
    walked: list[dict[str, Any]] = []
    accepted: Counter[str] = Counter()
    for row in sorted(fresh, key=lambda r: (r["repo"], rank(r))):
        if accepted[row["repo"]] >= PER_REPO:
            continue
        name = f"{row['repo'].replace('/', '__')}@{row['base_commit'][:12]}"
        corpus = {"url": f"https://github.com/{row['repo']}", "commit": row["base_commit"]}
        root = cb.corpus_root(name, corpus)
        cb.fetch({name: corpus})
        step = {"id": row["original_inst_id"], "repo": row["repo"]}
        step["py_files"] = len(list(root.rglob("*.py")))
        if step["py_files"] > MAX_PY_FILES:
            walked.append(step | {"outcome": "cost rule"})
            if name not in corpora:
                shutil.rmtree(root)
            continue
        gold = json.loads(row["gold_context"])
        verdicts = [
            cb.classify(root, g["file"], g["start_line"], g["end_line"], g["content"], row["patch"])
            for g in gold
        ]
        lines = cb.Lines(root)
        usable = [
            v
            for v in verdicts
            if v["class"] in cb.VERIFIED
            and lines.code(v.get("corrected_file", v["file"]), v["start"], v["end"])
        ]
        if not usable:
            walked.append(step | {"outcome": "no verified gold"})
            continue
        walked.append(step | {"outcome": "accepted"})
        accepted[row["repo"]] += 1
        corpora[name] = corpus
        audit[row["original_inst_id"]] = verdicts
        tasks.append(
            {
                "id": row["original_inst_id"],
                "split": SPLIT,
                "corpus": name,
                "query": row["problem_statement"],
                "symbols": [],
                "source": f"ContextBench full.parquet#{row['instance_id']}",
                "gold": [
                    {
                        "file": g["file"],
                        "start": g["start_line"],
                        "end": g["end_line"],
                        "sha256": cb._sha(cb.normalized(g["content"])),
                    }
                    for g in gold
                ],
            }
        )
    source = f"EuniAI/ContextBench data/full.parquet sha256 {cb.PARQUET_SHA256}"
    out = {
        "version": "ds3-tasks-v1",
        "sources": {"contextbench": source},
        "corpora": dict(sorted(corpora.items())),
        "tasks": tasks,
    }
    (HERE / "tasks.json").write_text(json.dumps(out, indent=1) + "\n")
    digest = cb._sha((HERE / "tasks.json").read_bytes())
    gold_audit = {
        "version": cb.AUDIT_VERSION,
        "tasks": f"docs/phase-6/decision-study-v3/tasks.json sha256 {digest}",
        "source": source,
        "verified": list(cb.VERIFIED),
        "spans": audit,
    }
    (HERE / "gold-audit.json").write_text(json.dumps(gold_audit, indent=1) + "\n")
    separation(rows, baseline, out, mentioned, fresh, walked)


def corpus_files(roots: list[Path], minimum: int = 512) -> set[str]:
    """sha256 of every ``*.py`` file of at least ``minimum`` bytes."""
    return {
        hashlib.sha256(data).hexdigest()
        for root in roots
        for p in root.rglob("*.py")
        if len(data := p.read_bytes()) >= minimum
    }


def separation(
    rows: list[dict[str, Any]],
    baseline: dict[str, Any],
    v3: dict[str, Any],
    mentioned: dict[str, int],
    fresh: list[dict[str, Any]],
    walked: list[dict[str, Any]],
) -> None:
    repos = {t["corpus"].split("@")[0].replace("__", "/") for t in v3["tasks"]}
    assert not repos & set(cb.ELIGIBLE), "a baseline repository in v3"
    assert not {t["id"] for t in baseline["tasks"]} & {t["id"] for t in v3["tasks"]}
    assert not {t["source"].split("#")[1] for t in v3["tasks"]} & set(mentioned)
    assert not set(baseline["corpora"]) & set(v3["corpora"])
    used = [cb.corpus_root(n, c) for n, c in baseline["corpora"].items()]
    mine = [cb.corpus_root(n, c) for n, c in v3["corpora"].items()]
    shared = corpus_files(used) & corpus_files(mine)
    by = Counter(w["repo"] for w in walked if w["outcome"] == "accepted")
    candidates = sum(r["repo"] not in cb.ELIGIBLE for r in rows)
    data = {
        "rule": {"split": SPLIT, "per_repo": PER_REPO, "max_py_files": MAX_PY_FILES, "seed": SEED},
        "scan": SCAN,
        "python_rows": len(rows),
        "excluded_baseline_repositories": sorted(cb.ELIGIBLE),
        "candidate_rows": candidates,
        "candidates_named_in_prior_artifacts": candidates - len(fresh),
        "fresh_by_repo": dict(sorted(Counter(r["repo"] for r in fresh).items())),
        "walked": walked,
        "accepted_by_repo": dict(sorted(by.items())),
        "checks": {
            "repositories_disjoint_from_baseline": True,
            "task_ids_disjoint_from_baseline": True,
            "task_ids_absent_from_prior_artifacts": True,
            "corpus_names_disjoint_from_baseline": True,
            "identical_py_files_ge_512_bytes_shared_with_baseline_corpora": len(shared),
        },
    }
    (HERE / "separation.json").write_text(json.dumps(data, indent=1) + "\n")
    n, c = len(v3["tasks"]), len(v3["corpora"])
    print(f"{n} tasks over {c} corpora; by repo {dict(by)}; shared files {len(shared)}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
