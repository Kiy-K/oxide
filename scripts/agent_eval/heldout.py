"""Held-out agent-eval tasks: pull requests merged on or after ``CUTOFF``, in the tasks.json schema.

    uv run python scripts/agent_eval/heldout.py      # from the repository root
    # equivalent: cd scripts/agent_eval && uv run python -m heldout

Writes ``fixtures/eval/agent-swe/heldout-tasks.json`` (kept tasks) and
``fixtures/eval/agent-swe/heldout-rejections.json`` (every PR the search returned, with a reason).
Run it under ``systemd-run --user --scope -p CPUQuota=200% -p MemoryMax=3G`` for the builds.

Selection, frozen with ``CUTOFF`` and ``QUOTA``:
  a. merged at or after ``CUTOFF`` (UTC), found by ``gh pr list --search merged:>=CUTOFF``;
  b. exactly one linked issue (GitHub ``closingIssuesReferences``: closing keywords or a manual
     link) whose body is non-empty; the problem text is its title and body;
  c. the diff touches a ``.py`` file under the repo's test dir (``REPOS``; ``tests/`` where
     ``REPOS`` gives none) and a non-test ``.py`` file;
  d. fail-to-pass non-empty: tests run before and after the gold patch in a fresh environment
     (``tasks.checkout``/``environment``/``apply``/``tests``). A PR is kept only if a second,
     independent measurement confirms every fail-to-pass test fails before and passes after,
     with the rule of ``tasks.verify_one``. Its pass-to-pass set (tests passing before and after)
     must be non-empty: otherwise "pass-to-pass empty after intersection" (added after run 2).
Screened PRs are processed in ``tasks.order`` (sha256 of the id); the first ``QUOTA`` that pass
are kept, two environment builds at a time. Candidates after the quota are recorded, not run.

Environment override (Main decision): every kept task carries ``build`` = ``BUILD``, and its
tests are verified with that recipe. The frozen ``REPOS`` recipe (Python 3.9, setuptools<70)
cannot build PRs merged after the cutoff: flask now requires Python >= 3.10, and setuptools<70
rejects PEP 639 string licenses. Dev tasks in tasks.json keep the frozen ``REPOS`` recipe.
``tasks.verify_one`` builds its task without ``build``, so ``verify`` here applies its rule
directly with the same helpers.

base_commit is the merge commit's first parent, so the gold patch is exactly the PR diff.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import tasks

CUTOFF = "2026-08-01"
QUOTA = 20
PARALLEL = 2  # concurrent environment builds
BUILD = {"python": "3.12", "setuptools": "setuptools>=77"}
DATASET = f"github-pr-merged-after:{CUTOFF}"
OUT = tasks.ROOT / "fixtures" / "eval" / "agent-swe"
HELDOUT = OUT / "heldout-tasks.json"
REJECTIONS = OUT / "heldout-rejections.json"
SCRATCH = tasks.CACHE / "heldout"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def gh(*args: str) -> Any:
    done = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if done.returncode:
        raise RuntimeError(f"gh {args[0]}: {done.stderr.strip()[-300:]}")
    return json.loads(done.stdout)


def git(mirror: Path, *args: str) -> str:
    done = tasks.sh("git", "--git-dir", mirror, *args, timeout=600)
    if done.returncode:
        raise RuntimeError(f"git {args[0]}: {done.stderr.strip()[-300:]}")
    return done.stdout


def has(mirror: Path, commit: str) -> bool:
    done = tasks.sh("git", "--git-dir", mirror, "cat-file", "-e", f"{commit}^{{commit}}")
    return done.returncode == 0


def test_dir(repo: str) -> str:
    return (tasks.REPOS[repo][1] or "tests") + "/"


def screen(repo: str, pr: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """A rejection reason, or ``""`` with the candidate that passes criteria a-c."""
    number, merged = pr["number"], pr["mergedAt"]
    if merged < f"{CUTOFF}T00:00:00Z":
        return f"merged before cutoff ({merged})", None
    linked = [i["number"] for i in pr["closingIssuesReferences"]]
    if len(linked) != 1:
        return f"linked issues: {len(linked)} (need exactly 1; {linked or 'none'})", None
    issue = gh("issue", "view", str(linked[0]), "-R", repo, "--json", "number,title,body")
    body = (issue["body"] or "").strip()
    if not body:
        return f"linked issue #{linked[0]} has an empty body", None
    merge = (pr.get("mergeCommit") or {}).get("oid")
    if not merge:
        return "no merge commit", None
    mirror = tasks._mirror(repo, merge)
    if not has(mirror, merge):
        fetched = tasks.sh(
            "git",
            "--git-dir",
            mirror,
            "fetch",
            "-q",
            "origin",
            "+refs/heads/*:refs/heads/*",
            timeout=1800,
        )
        if fetched.returncode or not has(mirror, merge):
            return f"merge commit {merge[:12]} unavailable in mirror: {fetched.stderr[-200:]}", None
    try:
        base = git(mirror, "rev-parse", f"{merge}^1").strip()
        names = git(mirror, "diff", "--no-renames", "--name-only", base, merge).split()
        d = test_dir(repo)
        if not [n for n in names if n.startswith(d) and n.endswith(".py")]:
            return f"diff touches no .py test file under {d}", None
        if not [n for n in names if not n.startswith(d) and n.endswith(".py")]:
            return "diff touches no non-test .py source file", None
        test_patch = git(
            mirror, "diff", "--no-renames", "--no-color", base, merge, "--", f":(top){d}"
        )
        gold = git(
            mirror, "diff", "--no-renames", "--no-color", base, merge, "--", f":(top,exclude){d}"
        )
    except RuntimeError as error:
        return f"git: {error}", None
    row = {
        "instance_id": f"{repo.replace('/', '__')}-{number}",
        "repo": repo,
        "base_commit": base,
        "version": tasks.REPOS[repo][0],
        "created_at": merged,
        "problem_statement": f"{issue['title']}\n{body}\n",
        "test_patch": test_patch,
        "patch": gold,
    }
    return "", {"id": row["instance_id"], "row": row}


def measure(row: dict[str, Any], scratch: Path) -> tuple[dict[str, str], dict[str, str], bool, str]:
    """Outcomes before and after the gold patch, from the first environment that builds
    (dependencies as of the merge date, then current ones).
    Returns (before, after, dated, reason)."""
    reason = ""
    for dated in (True, False):
        task: dict[str, Any] = {
            "id": row["instance_id"],
            "repo": row["repo"],
            "base_commit": row["base_commit"],
            "version": row["version"],
            "created_at": row["created_at"],
            "dated": dated,
            "build": BUILD,
            "problem": row["problem_statement"],
            "test_patch": row["test_patch"],
            "gold_patch": row["patch"],
        }
        work = scratch / f"{row['instance_id']}.{'dated' if dated else 'current'}"
        venv = work.parent / f"{work.name}.venv"
        try:
            tasks.checkout(task, work)
            env = tasks.environment(task, work, venv)
            files = tasks.changed(row["test_patch"])
            tasks.apply(work, row["test_patch"])
            before = tasks.tests(work, env, files)
            tasks.apply(work, row["patch"])
            after = tasks.tests(work, env, files)
            return before, after, dated, ""
        except (RuntimeError, subprocess.SubprocessError, OSError) as error:
            reason = f"setup: {error}"
        finally:
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(venv, ignore_errors=True)
    return {}, {}, False, reason


def check(
    row: dict[str, Any], scratch: Path, f2p: list[str], p2p: list[str]
) -> dict[str, Any] | str:
    """``tasks.verify_one``'s rule, measured with the build recipe: the task entry or a reason."""
    before, after, dated, reason = measure(row, scratch)
    if reason:
        return reason
    if not before or not after:
        return "no test results (timeout or no output)"
    if any(after.get(t) != "PASSED" for t in f2p):
        return "gold patch does not pass fail-to-pass here"
    if any(before.get(t) == "PASSED" for t in f2p):
        return "fail-to-pass already passes on the base commit"
    return {
        "id": row["instance_id"],
        "repo": row["repo"],
        "base_commit": row["base_commit"],
        "version": row["version"],
        "created_at": row["created_at"],
        "dated": dated,
        "build": BUILD,
        "problem": row["problem_statement"],
        "test_patch": row["test_patch"],
        "gold_patch": row["patch"],
        "fail_to_pass": f2p,
        "pass_to_pass": [t for t in p2p if before.get(t) == "PASSED" == after.get(t)],
        "test_files": tasks.changed(row["test_patch"]),
    }


def evaluate(row: dict[str, Any]) -> dict[str, Any] | str:
    """The frozen task entry, or the reason it is rejected at criterion d."""
    scratch = SCRATCH / row["instance_id"]
    try:
        before, after, _, reason = measure(row, scratch / "discover")
        if reason:
            return reason
        if not before or not after:
            return "no test results from the discovery run (timeout or no output)"
        f2p = sorted(t for t, o in after.items() if o == "PASSED" and before.get(t) != "PASSED")
        if not f2p:
            return (
                "fail-to-pass empty: no test passes after the gold patch that did not pass before"
            )
        p2p = sorted(t for t, o in after.items() if o == "PASSED" and before.get(t) == "PASSED")
        entry = check(row, scratch / "verify", f2p, p2p)
        if isinstance(entry, str):
            return f"second measurement: {entry}"
        if not entry["pass_to_pass"]:
            return "pass-to-pass empty after intersection"
        return entry
    except Exception as error:  # recorded as a rejection, never dropped silently
        return f"harness error: {type(error).__name__}: {str(error)[-300:]}"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> None:
    shutil.rmtree(SCRATCH, ignore_errors=True)
    queries: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    screened: list[dict[str, Any]] = []
    for repo in tasks.REPOS:
        queried = now()
        prs = sorted(
            gh(
                "pr",
                "list",
                "-R",
                repo,
                "--state",
                "merged",
                "--search",
                f"merged:>={CUTOFF}",
                "--limit",
                "1000",
                "--json",
                "number,title,mergedAt,mergeCommit,closingIssuesReferences",
            ),
            key=lambda p: p["number"],
        )
        if len(prs) >= 1000:
            raise RuntimeError(f"{repo}: search hit the 1000-result limit")
        queries.append(
            {
                "repo": repo,
                "search": f"merged:>={CUTOFF}",
                "queried_at": queried,
                "returned": len(prs),
            }
        )
        for pr in prs:
            reason, cand = screen(repo, pr)
            pr_id = f"{repo.replace('/', '__')}-{pr['number']}"
            rec = {"repo": repo, "pr": pr["number"], "id": pr_id, "merged_at": pr["mergedAt"]}
            rec["reason"] = reason or "screened: passes criteria a-c, awaiting criterion d"
            records.append(rec)
            by_id[pr_id] = rec
            if cand is not None:
                screened.append(cand)
        print(f"{repo}: {len(prs)} returned, {len(screened)} screened in so far", flush=True)

    screened.sort(key=lambda c: tasks.order({"id": c["id"]}))
    kept: list[dict[str, Any]] = []
    done = 0
    while done < len(screened) and len(kept) < QUOTA:
        batch = screened[done : done + PARALLEL]
        with ThreadPoolExecutor(PARALLEL) as pool:
            outcomes = list(pool.map(evaluate, [c["row"] for c in batch]))
        done += len(batch)
        for cand, outcome in zip(batch, outcomes, strict=True):
            rec = by_id[cand["id"]]
            if isinstance(outcome, str):
                rec["reason"] = outcome
                print(f"reject {cand['id']}: {outcome[:120]}", flush=True)
            elif len(kept) < QUOTA:
                kept.append(outcome)
                rec["reason"] = f"kept: rank {len(kept)} of {QUOTA} in sha256 order"
                print(f"keep {cand['id']}: {len(outcome['fail_to_pass'])} f2p", flush=True)
            else:
                rec["reason"] = f"not selected: {QUOTA} earlier qualifying PRs fill the quota"
    for cand in screened[done:]:
        by_id[cand["id"]]["reason"] = (
            f"not evaluated: {QUOTA} earlier qualifying PRs in sha256 order fill the quota"
        )

    assert len(kept) <= QUOTA and all(t["fail_to_pass"] for t in kept)
    OUT.mkdir(parents=True, exist_ok=True)
    HELDOUT.write_text(json.dumps({"dataset": DATASET, "tasks": kept}, indent=1) + "\n", "utf-8")
    header = {
        "generated_at": now(),
        "cutoff": CUTOFF,
        "quota": QUOTA,
        "build_override": BUILD,
        "build_note": (
            "held-out tasks use build; dev tasks in tasks.json keep the frozen REPOS recipe"
        ),
        "amendment": (
            "pass-to-pass must be non-empty after the before/after intersection "
            "(added after run 2; applied uniformly)"
        ),
        "queries": queries,
    }
    REJECTIONS.write_text(json.dumps({"header": header, "prs": records}, indent=1) + "\n", "utf-8")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print(f"kept {len(kept)} of {QUOTA} requested -> {HELDOUT}")


if __name__ == "__main__":
    main()
