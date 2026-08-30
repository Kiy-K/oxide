#!/usr/bin/env python3
"""Phase 3.1 client-compatibility spot checks: Claude Code + Codex CLI.

Not a statistical matrix (n=1 per cell) -- OpenCode carries the full A-E
matrix (run_matrix.py) since it is the only client with both a cheap Skill
mechanism and MCP support. This script exists only to confirm the B-vs-D
question replicates directionally on two more model families, and to
surface client-specific quirks (Codex has no Skill mechanism at all, so its
"B" is AGENTS.md-only and must never be pooled with OpenCode's Skill+
AGENTS.md B).
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OXIDE_BIN = ROOT / "target/release/oxide"
SKILL_SRC = ROOT / "skills/oxide-code-context/SKILL.md"
FIXTURES = {"py": ROOT / "fixtures/py_repo", "ts": ROOT / "fixtures/ts_repo"}
OUT = ROOT / "docs/evals/phase-3.1/client-compat-raw.jsonl"
LOG_DIR = ROOT / "docs/evals/phase-3.1/logs"

E1_AGENTS_CLI = (
    "## OXIDE\n\n"
    "For unfamiliar repository work where the implementation path is not\n"
    "already known, use `oxide context` before broad grep/read exploration.\n"
    "Use `oxide search` for focused follow-up discovery. For exact known-file\n"
    "or literal tasks, use normal tools directly. Read source before editing.\n"
)
D_AGENTS_MCP = (
    "## OXIDE\n\n"
    "For unfamiliar repository work where the implementation path is not\n"
    "already known, use OXIDE's context tool before broad grep/read\n"
    "exploration. Use its search tool for focused follow-up discovery. For\n"
    "exact known-file or literal tasks, use normal tools directly. Read\n"
    "source before editing.\n"
)

TASKS = [
    dict(id="A1", bucket="A", repo="py", prompt=(
        "There's a report that our HTTP client sometimes retries requests it "
        "shouldn't (e.g. permanent 4xx client errors), wasting time before "
        "giving up. Find where retry eligibility is decided in this repo and "
        "identify the exact check involved. Report the file and function "
        "name only — do not edit anything.")),
    dict(id="C1", bucket="C", repo="py", prompt=(
        "In `oxidepy/cache.py`, rename the `TTLCache` class to `TimedCache`. "
        "Only touch that one file.")),
]


def sh(cmd, cwd=None, env=None, timeout=180):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)


def setup_repo(repo_key, condition, run_dir):
    repo = run_dir / "repo"
    shutil.copytree(FIXTURES[repo_key], repo)
    sh([str(OXIDE_BIN), "index", str(repo), "--json"], timeout=60)
    binpath = run_dir / "bin"
    binpath.mkdir()
    if condition == "B":
        os.symlink(OXIDE_BIN, binpath / "oxide")
        (repo / "AGENTS.md").write_text(E1_AGENTS_CLI)
    elif condition == "D":
        (repo / "AGENTS.md").write_text(D_AGENTS_MCP)
    return repo, binpath


# ---------------- Claude Code ----------------

def run_claude(task, condition, rep):
    run_dir = Path(tempfile.mkdtemp(prefix=f"p31-claude-{task['id']}-{condition}-{rep}-"))
    try:
        repo, binpath = setup_repo(task["repo"], condition, run_dir)
        env = dict(os.environ)
        if condition == "B":
            env["PATH"] = f"{binpath}:" + env.get("PATH", "")
            if task["bucket"] != "skip-skill":
                skill_dst = repo / ".claude/skills/oxide-code-context"
                skill_dst.mkdir(parents=True)
                shutil.copy(SKILL_SRC, skill_dst / "SKILL.md")
        mcp_cfg = run_dir / "mcp.json"
        if condition == "D":
            mcp_cfg.write_text(json.dumps({
                "mcpServers": {"oxide": {"command": str(OXIDE_BIN), "args": ["mcp"]}}
            }))
        # NOTE: --add-dir takes a variadic list of directories, so it must
        # never sit immediately before the trailing prompt positional -- it
        # will greedily swallow the prompt text as another "directory".
        cmd = ["claude", "-p", task["prompt"], "--output-format", "json",
               "--dangerously-skip-permissions",
               "--add-dir", str(repo)]
        if condition == "D":
            cmd += ["--mcp-config", str(mcp_cfg), "--strict-mcp-config"]

        start = time.time()
        try:
            r = sh(cmd, cwd=str(repo), env={**env, "PWD": str(repo)}, timeout=180)
            stdout, stderr, rc, timed_out = r.stdout, r.stderr, r.returncode, False
        except subprocess.TimeoutExpired as e:
            stdout = (e.stdout or "") if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
            stderr = (e.stderr or "") if isinstance(e.stderr, str) else (e.stderr or b"").decode("utf-8", "replace")
            rc, timed_out = -1, True
        wall = round(time.time() - start, 1)

        log_name = f"claude-{task['id']}-{condition}-r{rep}.json"
        (LOG_DIR / log_name).write_text(stdout or "")
        if stderr:
            (LOG_DIR / (log_name + ".stderr")).write_text(stderr[-4000:])

        return dict(client="claude-code", task=task["id"], bucket=task["bucket"], condition=condition,
                    rep=rep, wall_s=wall, rc=rc, timed_out=timed_out,
                    stdout_tail=(stdout or "")[-1500:])
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


# ---------------- Codex ----------------

REAL_CODEX_HOME = Path.home() / ".codex"


def run_codex(task, condition, rep):
    """First attempt isolated CODEX_HOME with no credentials -> 401
    Unauthorized on every real model call. Root cause (confirmed, not
    assumed): `codex` here authenticates via ChatGPT login
    (`codex-companion.mjs setup --json` reports `authMethod: chatgpt`,
    credential stored in `~/.codex/auth.json`), and a from-scratch isolated
    CODEX_HOME has no such file. Fixed by copying auth.json into the
    isolated home (keeping config.toml isolated so no ambient MCP servers
    or AGENTS.md leak in) -- this is a self-inflicted harness bug, not an
    environment-wide auth gap, and is recorded as such rather than as
    "Codex requires an API key" (it doesn't, in this environment)."""
    run_dir = Path(tempfile.mkdtemp(prefix=f"p31-codex-{task['id']}-{condition}-{rep}-"))
    codex_home = run_dir / "codex_home"
    codex_home.mkdir()
    auth_src = REAL_CODEX_HOME / "auth.json"
    if auth_src.exists():
        shutil.copy(auth_src, codex_home / "auth.json")
    try:
        repo, binpath = setup_repo(task["repo"], condition, run_dir)
        env = dict(os.environ)
        env["CODEX_HOME"] = str(codex_home)
        if condition == "B":
            env["PATH"] = f"{binpath}:" + env.get("PATH", "")
        if condition == "D":
            add = sh(["codex", "mcp", "add", "oxide", "--", str(OXIDE_BIN), "mcp"], env=env, timeout=30)
            if add.returncode != 0:
                return dict(client="codex", task=task["id"], bucket=task["bucket"], condition=condition,
                            rep=rep, rc=add.returncode, timed_out=False,
                            error=f"mcp add failed: {add.stderr[-500:]}")

        cmd = ["codex", "exec", "--sandbox", "workspace-write", "--skip-git-repo-check",
               "--json", "-C", str(repo), task["prompt"]]
        start = time.time()
        try:
            r = sh(cmd, cwd=str(repo), env={**env, "PWD": str(repo)}, timeout=180)
            stdout, stderr, rc, timed_out = r.stdout, r.stderr, r.returncode, False
        except subprocess.TimeoutExpired as e:
            stdout = (e.stdout or "") if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
            stderr = (e.stderr or "") if isinstance(e.stderr, str) else (e.stderr or b"").decode("utf-8", "replace")
            rc, timed_out = -1, True
        wall = round(time.time() - start, 1)

        log_name = f"codex-{task['id']}-{condition}-r{rep}.jsonl"
        (LOG_DIR / log_name).write_text(stdout or "")
        if stderr:
            (LOG_DIR / (log_name + ".stderr")).write_text(stderr[-4000:])

        return dict(client="codex", task=task["id"], bucket=task["bucket"], condition=condition,
                    rep=rep, wall_s=wall, rc=rc, timed_out=timed_out,
                    stdout_tail=(stdout or "")[-1500:])
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT, "a") as f:
        for task in TASKS:
            for condition in ("B", "D"):
                for rep in range(1):
                    for fn, name in ((run_claude, "claude"), (run_codex, "codex")):
                        try:
                            rec = fn(task, condition, rep)
                        except Exception as e:
                            rec = dict(client=name, task=task["id"], condition=condition, rep=rep, error=str(e))
                        f.write(json.dumps(rec) + "\n")
                        f.flush()
                        print(f"[{name}] {task['id']} {condition} r{rep} -> "
                              f"rc={rec.get('rc')} timed_out={rec.get('timed_out')} "
                              f"err={rec.get('error')}", flush=True)


if __name__ == "__main__":
    main()
