"""Executable task set: SWE-bench Verified instances whose tests run here.

``fetch`` narrows the pinned dataset to the repositories this harness can build.
``verify`` keeps an instance only when, in a fresh environment built from its base commit,
the dataset's fail-to-pass tests fail before the gold patch and pass after it. Its pass-to-pass
set is cut to the tests that pass both before and after, so an environment gap cannot
count against an arm.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
# Dev: the dataset-pinned tasks every run to date has used (spent, not held-out).
TASKS = ROOT / "fixtures" / "eval" / "agent-swe" / "tasks.json"
# Held-out: built from PRs merged after a cutoff, opened once per set (see heldout.py).
HELDOUT = ROOT / "fixtures" / "eval" / "agent-swe" / "heldout-tasks.json"
CACHE = Path(os.environ.get("OXIDE_EVAL_CACHE", Path.home() / ".cache" / "oxide" / "agent-eval"))
MIRRORS = Path.home() / ".cache" / "oxide" / "contextbench" / "git"  # bare, from contextbench.py
DATASET = "princeton-nlp/SWE-bench_Verified@c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
# Pure-Python repositories whose tests are pytest node ids; Python as SWE-bench builds them.
REPOS = {
    "pytest-dev/pytest": ("3.9", "testing"),
    "psf/requests": ("3.9", None),
    "pallets/flask": ("3.9", None),
    "pylint-dev/pylint": ("3.9", None),
    "mwaskom/seaborn": ("3.9", None),
}
RESULT = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) (\S.*?)(?: - .*)?$")


def path_for(split: str) -> Path:
    if split not in {"dev", "heldout"}:
        raise ValueError(f"unknown split {split!r}")
    return TASKS if split == "dev" else HELDOUT


def load(split: str = "dev") -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = json.loads(path_for(split).read_text(encoding="utf-8"))["tasks"]
    return tasks


def order(task: dict[str, Any]) -> str:
    """The predeclared, seed-free task order for ``run --tasks N``."""
    return hashlib.sha256(task["id"].encode()).hexdigest()


def sh(
    *argv: str | Path,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(a) for a in argv],
        cwd=cwd,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def fetch(parquet: Path, out: Path) -> None:
    """Candidate rows of ``REPOS`` from the dataset's parquet (needs ``--with pyarrow``)."""
    rows = importlib.import_module("pyarrow.parquet").read_table(parquet).to_pylist()
    keep = [r for r in rows if r["repo"] in REPOS]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(keep), encoding="utf-8")
    print(f"{len(keep)} candidates of {len(rows)} rows -> {out}")


_CLONE = threading.Lock()


def _mirror(repo: str, commit: str) -> Path:
    """A bare repository holding ``commit``: the contextbench mirror if it has it, else a
    full clone of its own, made once."""
    name = repo.replace("/", "__") + ".git"
    shared = MIRRORS / name
    has = ["git", "--git-dir", str(shared), "cat-file", "-e", f"{commit}^{{commit}}"]
    if shared.exists() and subprocess.run(has, capture_output=True, check=False).returncode == 0:
        return shared
    with _CLONE:
        if not (CACHE / "git" / name).exists():
            (CACHE / "git").mkdir(parents=True, exist_ok=True)
            url = f"https://github.com/{repo}"
            subprocess.run(
                ["git", "clone", "-q", "--bare", url, str(CACHE / "git" / name)], check=True
            )
    return CACHE / "git" / name


def checkout(task: dict[str, Any], work: Path) -> None:
    """The base commit as a one-commit repository: no history, no remote, nothing to look up."""
    mirror = _mirror(task["repo"], task["base_commit"])
    work.mkdir(parents=True)
    archive = subprocess.run(
        ["git", "--git-dir", str(mirror), "archive", task["base_commit"]],
        capture_output=True,
        check=True,
    )
    subprocess.run(["tar", "-x", "-C", str(work)], input=archive.stdout, check=True)
    git = ["git", "-c", "user.name=eval", "-c", "user.email=eval@localhost"]
    subprocess.run([*git, "init", "-q"], cwd=work, check=True)
    subprocess.run([*git, "add", "-A"], cwd=work, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "base"], cwd=work, check=True)


def environment(task: dict[str, Any], work: Path, venv: Path) -> dict[str, str]:
    """A venv with the checkout installed editable; the env for processes that use it.
    A ``dated`` task resolves dependencies as of the issue's date. A task's ``build`` block
    (``python``, ``setuptools``) overrides the REPOS recipe; dev tasks carry none."""
    python, extra = REPOS[task["repo"]]
    build = task.get("build", {})
    python = build.get("python", python)
    setuptools = build.get("setuptools", "setuptools<70")
    env = {"SETUPTOOLS_SCM_PRETEND_VERSION": task["version"], "VIRTUAL_ENV": str(venv)}
    made = sh("uv", "venv", "-q", "--python", python, venv)
    if made.returncode:
        raise RuntimeError(f"venv: {made.stderr[-400:]}")

    def install(*args: str, era: bool = True) -> subprocess.CompletedProcess[str]:
        argv = ["uv", "pip", "install", "-q", "-p", str(venv / "bin" / "python")]
        date = ["--exclude-newer", task["created_at"]] if era and task["dated"] else []
        return sh(*argv, *date, *args, env=env)

    # Dev recipe: setuptools stays below 70 because old pytest and plugins import pkg_resources.
    # A held-out recipe names its own setuptools, verified with that recipe.
    install(setuptools, "wheel", "setuptools_scm", "flit_core", era=False)
    install("pytest")
    for spec in (f"{work}[{extra}]", str(work)) if extra else (str(work),):
        done = install("--no-build-isolation", "-e", spec)
        if done.returncode == 0:
            return {**env, "PATH": f"{venv / 'bin'}{os.pathsep}{os.environ['PATH']}"}
    raise RuntimeError(f"install: {done.stderr[-400:]}")


def changed(patch: str) -> list[str]:
    return sorted(set(re.findall(r"^\+\+\+ b/(\S+)", patch, re.MULTILINE)))


def apply(work: Path, patch: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".diff", encoding="utf-8", delete=False) as f:
        f.write(patch)
    try:
        done = sh("git", "apply", "--whitespace=nowarn", f.name, cwd=work)
    finally:
        os.unlink(f.name)
    if done.returncode:
        raise RuntimeError(f"patch: {done.stderr[-400:]}")


def tests(work: Path, env: dict[str, str], files: list[str], timeout: int = 900) -> dict[str, str]:
    """Outcome per node id of the given test files (pytest ``-rA`` summary)."""
    argv = ["pytest", "-rA", "-q", "-p", "no:cacheprovider", *files]
    try:
        done = sh(*argv, cwd=work, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {}
    out: dict[str, str] = {}
    for line in done.stdout.splitlines():
        hit = RESULT.match(line)
        if hit:
            out[hit[2]] = hit[1]
    return out


def verify_one(row: dict[str, Any], scratch: Path) -> dict[str, Any] | str:
    """The frozen task entry, or the reason the instance does not run here. Dependencies as of
    the issue's date are tried first (the era the project ran in), then current ones."""
    f2p, p2p = json.loads(row["FAIL_TO_PASS"]), json.loads(row["PASS_TO_PASS"])
    reason = ""
    for dated in (True, False):
        task: dict[str, Any] = {
            "id": row["instance_id"],
            "repo": row["repo"],
            "base_commit": row["base_commit"],
            "version": row["version"],
            "created_at": row["created_at"],
            "dated": dated,
            "problem": row["problem_statement"],
            "test_patch": row["test_patch"],
            "gold_patch": row["patch"],
        }
        work = scratch / f"{row['instance_id']}.{'dated' if dated else 'current'}"
        try:
            checkout(task, work)
            env = environment(task, work, work.parent / f"{work.name}.venv")
            files = changed(row["test_patch"])
            apply(work, row["test_patch"])
            before = tests(work, env, files)
            apply(work, row["patch"])
            after = tests(work, env, files)
        except (RuntimeError, subprocess.SubprocessError, OSError) as error:
            reason = f"setup: {error}"
            continue
        if not f2p or any(after.get(t) != "PASSED" for t in f2p):
            reason = "gold patch does not pass fail-to-pass here"
        elif any(before.get(t) == "PASSED" for t in f2p):
            reason = "fail-to-pass already passes on the base commit"
        else:
            task["fail_to_pass"] = f2p
            task["pass_to_pass"] = [t for t in p2p if before.get(t) == "PASSED" == after.get(t)]
            task["test_files"] = files
            return task
    return reason


def verify(candidates: Path, jobs: int, only: list[str]) -> None:
    rows = [r for r in json.loads(candidates.read_text(encoding="utf-8"))]
    rows = [r for r in rows if not only or r["instance_id"] in only]
    scratch = CACHE / "verify"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    kept: list[dict[str, Any]] = []
    with ThreadPoolExecutor(jobs) as pool:
        for row, result in zip(rows, pool.map(lambda r: verify_one(r, scratch), rows), strict=True):
            if isinstance(result, str):
                print(f"drop {row['instance_id']}: {result}")
            else:
                print(
                    f"keep {row['instance_id']}: {len(result['fail_to_pass'])} f2p, "
                    f"{len(result['pass_to_pass'])} p2p"
                )
                kept.append(result)
    kept.sort(key=order)
    TASKS.parent.mkdir(parents=True, exist_ok=True)
    TASKS.write_text(json.dumps({"dataset": DATASET, "tasks": kept}, indent=1) + "\n", "utf-8")
    print(f"{len(kept)} of {len(rows)} instances verified -> {TASKS}")
