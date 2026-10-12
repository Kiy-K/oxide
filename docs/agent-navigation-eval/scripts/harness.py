#!/usr/bin/env python3
"""Agent-guided navigation experiment (docs/agent-navigation-eval/README.md).

Same coding agent (Codex CLI, fixed model and effort, isolated CODEX_HOME),
same SWE-bench Verified tasks and snapshots, four OXIDE conditions:

  A  agent only
  B  + packed ContextBundle (payload, 4,096 units)
  C  + NavigationBundle (locations, previews, typed relations; no bodies)
  D  C + bounded on-demand OXIDE navigation CLI (find/expand/route)

Subcommands:
  tasks                       write tasks.json (frozen task list) from DecisionBench v1
  prepare [ids...]            export source, index, render bundles
  run SPLIT VARIANTS REP [N]  run the agent, N tasks in parallel (variant order randomized per task)
  grade SPLIT REP             SWE-bench harness on every finished run's patch

Data lives in $OXIDE_AGENTNAV_DATA (default ~/Projects/oxide-eval-data/oxide-agentnav);
it holds source copies, stores, transcripts and patches, so it stays out of git.
"""
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("OXIDE_AGENTNAV_DATA",
                           Path.home() / "Projects/oxide-eval-data/oxide-agentnav"))
EVAL = Path.home() / "Projects/oxide-eval-data"
PARQUET = EVAL / "oxide-jev-eval/contextbench-full.parquet"
WORKTREES = [EVAL / "oxide-capremoval-eval/cb-wt", EVAL / "oxide-jev-eval/cb-wt"]
BIN = REPO / "oxide_kernel/target/release/examples/agent_nav"
CODEX_HOME = DATA / "codex-home"
DOCKER_HOST = "unix:///run/user/1000/podman/podman.sock"

MODEL, EFFORT = "gpt-6-luna", "high"
BUDGET = 4096          # B's ContextBundle budget (oxide-units-v1), the largest frozen Phase 5 budget
TIMEOUT_S = 30 * 60    # wall-clock cap per agent run, every variant
INDEX_CAP = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=6G",
             "-p", "MemorySwapMax=0", "-p", "CPUQuota=200%"]
VARIANTS = "ABCDE"  # E: dev-only iteration, D's operations as MCP tools
# A minimal environment: the agent's shell (`bash -lc`) gets an empty HOME and a fixed
# PATH, never the user's profile or credentials beyond Codex's own auth file.
AGENT_ENV = {"CODEX_HOME": str(CODEX_HOME), "HOME": str(DATA / "home"), "LANG": "C.UTF-8",
             "USER": os.environ.get("USER", "agent"),
             "PATH": f"{Path(shutil.which('codex') or '/usr/bin').parent}:/usr/local/bin:/usr/bin"}

BASE_PROMPT = """You are working in a checkout of the {repo} repository (the current directory). \
Resolve the issue below by editing the repository's source code.

<issue>
{issue}
</issue>

Make the smallest source change that resolves the issue; you do not need to add tests. \
There is no network access, so do not try to install packages. When you are done, \
reply with a short summary of your change."""

B_PROMPT = """

OXIDE, a repository context engine, packed this source context for the issue. \
It may be incomplete or include irrelevant code.
<oxide_context>
{bundle}</oxide_context>"""

C_PROMPT = """

OXIDE, a repository navigation engine, suggests these locations for the issue. \
They may be incomplete or irrelevant; read the files yourself as needed.
<oxide_navigation>
{nav}</oxide_navigation>"""

D_PROMPT = """
You can also query OXIDE's index of this checkout. It is read-only and returns \
locations and typed relations, never source bodies or judgments:
  {tool} find NAME                    declarations named exactly NAME
  {tool} expand REF [KIND] [DIR]      relations of REF (a path, or path::Qualified.name);
                                      KIND: calls|refs|imports|tests|contains|implements|all (default all),
                                      DIR: in|out|both (default both)
  {tool} route "QUERY"                a fresh navigation list for another query
Use it if it helps."""

E_PROMPT = """
OXIDE's index of this checkout is also available through the oxide_find, oxide_expand and \
oxide_route tools. They are read-only and return locations and typed relations, never source \
bodies or judgments. Use them if they help."""


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


# Excluded before the freeze: the gold patch cannot be graded here (preregistration.md).
EXCLUDED = {i: "SWE-bench image does not unpack under rootless podman (file UID 197609 "
               "outside the user's subordinate UID range); gold validation errored"
            for i in ("matplotlib__matplotlib-24026", "matplotlib__matplotlib-25332")}


def tasks():
    return [t for t in json.loads((HERE / "tasks.json").read_text())["tasks"] if "excluded" not in t]


def cmd_tasks():
    """Frozen list: ContextBench SWE-bench Verified Python tasks of DecisionBench v1,
    with its repository splits (dev -> dev; calibration and test -> heldout)."""
    db = REPO / "docs/phase-5/decisionbench-v1"
    cb = json.loads((db / "contextbench-tasks.json").read_text())["tasks"]
    splits = json.loads((db / "splits.json").read_text())["repositories"]
    out = [{"id": t["id"], "instance_id": t["original_id"], "repo": t["repo"],
            "base_commit": t["base_commit"],
            "split": "dev" if splits[t["repo"]] == "dev" else "heldout"}
           for t in cb if t["benchmark_source"] == "Verified"]
    for t in out:
        if t["instance_id"] in EXCLUDED:
            t["excluded"] = EXCLUDED[t["instance_id"]]
    out.sort(key=lambda t: (t["split"], t["id"]))
    doc = {"format": "oxide-agentnav-tasks-v1",
           "source": "docs/phase-5/decisionbench-v1/contextbench-tasks.json, benchmark_source == Verified",
           "splits": "DecisionBench v1 repository splits: dev -> dev; calibration, test -> heldout",
           "tasks": out}
    (HERE / "tasks.json").write_text(json.dumps(doc, indent=1) + "\n")
    print({s: sum(t["split"] == s and "excluded" not in t for t in out) for s in ("dev", "heldout")})


def contextbench_rows(ids):
    import pyarrow.parquet as pq
    rows = pq.read_table(PARQUET).to_pylist()
    return {r["instance_id"]: r for r in rows if r["instance_id"] in ids}


def cmd_prepare(ids):
    todo = [t for t in tasks() if not ids or t["id"] in ids]
    rows = contextbench_rows({t["id"] for t in todo})
    for t in todo:
        d = DATA / "tasks" / t["id"]
        if (d / "prep.json").exists():
            continue
        row = rows[t["id"]]
        wt = next(w / t["id"] for w in WORKTREES if (w / t["id"]).is_dir())
        head = sh(["git", "-C", str(wt), "rev-parse", "HEAD"]).stdout.strip()
        assert head == t["base_commit"], (t["id"], head)
        shutil.rmtree(d / "src", ignore_errors=True)
        (d / "src").mkdir(parents=True)
        # Tracked files at base_commit only: no history, no untracked state.
        archive = subprocess.run(["git", "-C", str(wt), "archive", t["base_commit"]],
                                 check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(d / "src")], input=archive, check=True)
        (d / "issue.txt").write_text(row["problem_statement"])
        gold = row["gold_context"]
        (d / "gold.json").write_text(json.dumps(
            {"gold_context": json.loads(gold) if isinstance(gold, str) else gold,
             "patch": row["patch"], "f2p": row["f2p"], "p2p": row["p2p"]}))
        index = json.loads(sh(INDEX_CAP + [str(BIN), "index", str(d / "src"), str(d / "store"),
                                           "agentnav-" + t["repo"].replace("/", "-")]).stdout)
        t0 = time.time()
        sh([str(BIN), "bundle", str(d / "store"), str(d / "issue.txt"), str(BUDGET), str(d / "oxide")])
        prep = {"task": t, "index": index, "bundle_wall_s": time.time() - t0,
                "bundle": json.loads((d / "oxide/run.json").read_text()),
                "issue_sha256": hashlib.sha256(row["problem_statement"].encode()).hexdigest(),
                "archive_sha256": hashlib.sha256(archive).hexdigest()}
        (d / "prep.json").write_text(json.dumps(prep, indent=1))
        print(t["id"], index["entities"], f'{index["index_seconds"]:.1f}s',
              prep["bundle"]["bundle_units"], prep["bundle"]["nav_units"], flush=True)


def prompt(t, variant, run_dir):
    d = DATA / "tasks" / t["id"]
    text = BASE_PROMPT.format(repo=t["repo"], issue=(d / "issue.txt").read_text().strip())
    if variant == "B":
        text += B_PROMPT.format(bundle=(d / "oxide/bundle.txt").read_text())
    if variant in "CDE":
        text += C_PROMPT.format(nav=(d / "oxide/nav.txt").read_text())
    if variant == "D":
        text += D_PROMPT.format(tool=run_dir / "oxide-nav")
    if variant == "E":
        text += E_PROMPT
    return text


GIT_ENV = {"GIT_AUTHOR_NAME": "base", "GIT_AUTHOR_EMAIL": "base@example.invalid",
           "GIT_COMMITTER_NAME": "base", "GIT_COMMITTER_EMAIL": "base@example.invalid",
           "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"}


def run_one(t, variant, rep):
    d = DATA / "tasks" / t["id"]
    run_dir = DATA / "runs" / f"r{rep}" / variant / t["id"]
    if (run_dir / "meta.json").exists():
        return
    shutil.rmtree(run_dir, ignore_errors=True)
    repo = run_dir / "repo"
    shutil.copytree(d / "src", repo, symlinks=True)
    # A fresh one-commit repository: `git diff` works, upstream history is absent.
    for c in (["init", "-q"], ["add", "-A"], ["commit", "-q", "--no-verify", "-m", "base"]):
        sh(["git", "-C", str(repo)] + c, env={**os.environ, **GIT_ENV})
    extra = []
    if variant == "D":
        tool = run_dir / "oxide-nav"
        tool.write_text(f'#!/bin/bash\ncmd="$1"; shift\n'
                        f'exec env OXIDE_NAV_LOG="{run_dir}/nav-calls.jsonl" "{BIN}" "$cmd" "{d}/store" "$@"\n')
        tool.chmod(0o755)
        # The store lock and the call log need write access inside the sandbox.
        extra = ["--add-dir", str(d / "store"), "--add-dir", str(run_dir)]
    if variant == "E":
        # Codex runs MCP servers outside the command sandbox.
        server = [str(HERE / "scripts/nav_mcp.py"), str(BIN), str(d / "store"), str(run_dir / "nav-calls.jsonl")]
        extra = ["-c", 'mcp_servers.oxide.command="python3"', "-c", f"mcp_servers.oxide.args={json.dumps(server)}"]
    text = prompt(t, variant, run_dir)
    (run_dir / "prompt.txt").write_text(text)
    cmd = ["timeout", str(TIMEOUT_S), "codex", "exec", "--json", "--ephemeral", "--skip-git-repo-check",
           "-m", MODEL, "-c", f'model_reasoning_effort="{EFFORT}"', "-C", str(repo),
           "-o", str(run_dir / "last.txt"), *extra, text]
    t0 = time.time()
    with open(run_dir / "events.jsonl", "w") as out, open(run_dir / "stderr.txt", "w") as err:
        code = subprocess.run(cmd, stdout=out, stderr=err, stdin=subprocess.DEVNULL, env=AGENT_ENV).returncode
    wall = time.time() - t0
    sh(["git", "-C", str(repo), "add", "-A"])
    patch = sh(["git", "-C", str(repo), "diff", "--cached", "HEAD", "--", ".",
                ":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc"]).stdout
    (run_dir / "patch.diff").write_text(patch)
    meta = {"task": t["id"], "instance_id": t["instance_id"], "variant": variant, "rep": rep,
            "model": MODEL, "effort": EFFORT, "exit_code": code, "timed_out": code == 124,
            "wall_s": wall, "patch_bytes": len(patch),
            "prompt_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "codex_version": sh(["codex", "--version"]).stdout.strip(),
            "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0))}
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=1))
    shutil.rmtree(repo)  # the patch and the transcript are the record
    print(t["id"], variant, f"r{rep}", f"exit={code}", f"{wall:.0f}s", f"patch={len(patch)}B", flush=True)


def cmd_run(split, variants, rep, workers):
    todo = [t for t in tasks() if t["split"] == split]
    random.Random(f"oxide-agentnav/{split}/r{rep}").shuffle(todo)

    def task_runs(t):
        order = list(variants)
        random.Random(f"oxide-agentnav/{t['id']}/r{rep}").shuffle(order)
        for v in order:  # sequential per task: D's store has one lock owner
            run_one(t, v, rep)

    with ThreadPoolExecutor(workers) as pool:
        for f in [pool.submit(task_runs, t) for t in todo]:
            f.result()


def cmd_grade(split, rep):
    runs = DATA / "runs" / f"r{rep}"
    ids = {t["instance_id"] for t in tasks() if t["split"] == split}
    gdir = DATA / "grading"
    gdir.mkdir(exist_ok=True)
    for v in VARIANTS:
        preds = []
        for meta in sorted((runs / v).glob("*/meta.json")):
            m = json.loads(meta.read_text())
            if m["instance_id"] in ids:
                preds.append({"instance_id": m["instance_id"], "model_name_or_path": f"{v}-r{rep}",
                              "model_patch": (meta.parent / "patch.diff").read_text()})
        if not preds:
            continue
        pfile = gdir / f"preds-{v}-r{rep}-{split}.jsonl"
        pfile.write_text("".join(json.dumps(p) + "\n" for p in preds))
        subprocess.run([str(DATA / "venv/bin/python"), "-m", "swebench.harness.run_evaluation",
                        "--dataset_name", "SWE-bench/SWE-bench_Verified",
                        "--predictions_path", str(pfile), "--max_workers", "1",
                        "--run_id", f"agentnav-{v}-r{rep}-{split}",
                        "--timeout", "1800"],
                       cwd=gdir, check=True, env={**os.environ, "DOCKER_HOST": DOCKER_HOST})


if __name__ == "__main__":
    cmd, *args = sys.argv[1:] or ["help"]
    if cmd == "tasks":
        cmd_tasks()
    elif cmd == "prepare":
        cmd_prepare(set(args))
    elif cmd == "run":
        cmd_run(args[0], args[1], int(args[2]), int(args[3]) if len(args) > 3 else 2)
    elif cmd == "grade":
        cmd_grade(args[0], int(args[1]))
    else:
        sys.exit(__doc__)
