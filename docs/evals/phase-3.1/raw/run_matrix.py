#!/usr/bin/env python3
"""Phase 3.1 runner: CLI vs MCP vs both, across conditions A-E.

Conditions (see protocol.md for full rationale):
  A - native baseline: no oxide involved at all
  B - CLI production: oxide on PATH + skills/oxide-code-context/SKILL.md +
      validated E1 AGENTS.md rule (Phase 2.3's winning CLI condition)
  C - MCP only, minimal guidance: real rmcp `oxide mcp` server registered,
      no AGENTS.md rule, no skill, no CLI binary on PATH
  D - MCP + validated persistent guidance: same MCP registration as C, plus
      an AGENTS.md rule adapted from E1's wording to refer generically to
      OXIDE's tools instead of literal CLI syntax
  E - CLI + MCP simultaneously: B's full CLI surface plus C/D's MCP
      registration, unmodified (no extra MCP-specific wording added)

Every condition disables the ambient `codegraph` / `codebase-memory-mcp`
MCP servers explicitly in an isolated OPENCODE_CONFIG (Phase 2.3 found
`--pure` alone does not remove `codegraph`).

Resumable: results are appended to results.jsonl keyed by (task, condition,
rep); re-running skips keys already present unless --force.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OXIDE_BIN = ROOT / "target/release/oxide"
SKILL_SRC = ROOT / "skills/oxide-code-context/SKILL.md"
MODEL = os.environ.get("P31_MODEL", "opencode/muse-spark-1.2-contributor-free")
# gpt-5.6-luna hangs indefinitely on every tool call without --auto (Phase
# 2.3 finding, reconfirmed this phase); muse-spark does not need it.
EXTRA_ARGS = ["--auto"] if "gpt-5.6-luna" in MODEL else []
OUT_DIR = ROOT / "docs/evals/phase-3.1"
LOG_DIR = OUT_DIR / "logs"
RESULTS_PATH = OUT_DIR / "results.jsonl"
FIXTURES = {"py": ROOT / "fixtures/py_repo", "ts": ROOT / "fixtures/ts_repo"}

CONDITIONS = ["A", "B", "C", "D", "E"]

E1_AGENTS_CLI = (
    "## OXIDE\n\n"
    "For unfamiliar repository work where the implementation path is not\n"
    "already known, use `oxide context` before broad grep/read exploration.\n"
    "Use `oxide search` for focused follow-up discovery. For exact known-file\n"
    "or literal tasks, use normal tools directly. Read source before editing.\n"
)

# D: same principle as E1, wording adapted to be transport-generic per the
# phase brief ("refer generically to OXIDE rather than exact CLI syntax").
D_AGENTS_MCP = (
    "## OXIDE\n\n"
    "For unfamiliar repository work where the implementation path is not\n"
    "already known, use OXIDE's context tool before broad grep/read\n"
    "exploration. Use its search tool for focused follow-up discovery. For\n"
    "exact known-file or literal tasks, use normal tools directly. Read\n"
    "source before editing.\n"
)

NAV_TASKS = [
    dict(id="A1", bucket="A", repo="py", edit=False, prompt=(
        "There's a report that our HTTP client sometimes retries requests it "
        "shouldn't (e.g. permanent 4xx client errors), wasting time before "
        "giving up. Find where retry eligibility is decided in this repo and "
        "identify the exact check involved. Report the file and function "
        "name only — do not edit anything.")),
    dict(id="A2", bucket="A", repo="py", edit=False, prompt=(
        "Some users report getting stale cached data back even though it "
        "should have expired by now. Find where cache expiration is "
        "implemented in this repo and describe how expiry is checked. "
        "Report the file and function only — do not edit anything.")),
    dict(id="A3", bucket="A", repo="ts", edit=False, prompt=(
        "We refresh an auth token somewhere after it goes stale, but nobody "
        "remembers where that logic lives or what triggers it. Find it and "
        "report the file, the function, and what calls it. Do not edit "
        "anything.")),
    dict(id="A4", bucket="A", repo="ts", edit=False, prompt=(
        "The API client's retry backoff delay doesn't seem to grow the way "
        "engineers expect for the first couple of retries. Find where the "
        "backoff delay is computed and what implements the retry policy. "
        "Report the file and function only — do not edit anything.")),
    dict(id="B1", bucket="B", repo="py", edit=False, prompt=(
        "Somewhere in this repo's retry logic there's a test that checks the "
        "retry policy gives up after exhausting all attempts. Find that test "
        "and report which file and test function it is.")),
    dict(id="B2", bucket="B", repo="ts", edit=False, prompt=(
        "This repo has a `VersionedStore` class for tracking versioned "
        "values. Find every other file in the repo that imports or uses it, "
        "and report which ones (or report none, if there are none).")),
    dict(id="C1", bucket="C", repo="py", edit=True, prompt=(
        "In `oxidepy/cache.py`, rename the `TTLCache` class to `TimedCache`. "
        "Only touch that one file.")),
    dict(id="C2", bucket="C", repo="ts", edit=True, prompt=(
        "In `src/ui/Button.tsx`, add a one-line comment directly above the "
        "component saying `// TODO: memoize`. Only touch that one file.")),
    dict(id="C3", bucket="C", repo="py", edit=True, prompt=(
        'In `oxidepy/http_client.py`, add a module-level docstring line at '
        'the very top if one is not already present: `"""Thin HTTP client '
        'wrapper."""`. Only touch that one file.')),
    dict(id="C4", bucket="C", repo="ts", edit=True, prompt=(
        "In `src/net/retry.ts`, rename the exported const "
        "`defaultRetryPolicy` to `DEFAULT_RETRY_POLICY`. Only touch that one "
        "file.")),
]

CODING_TASKS = [
    dict(id="coding-py", kind="coding", lang="py", src=ROOT / "eval-agent/tasks/py_bug_retry",
         verify="verify.sh", prompt=(
             "There's a bug reported against this repo's retry/backoff logic: "
             "clients are hammering the server harder on each retry instead of "
             "backing off. Find the bug and fix it. Do not change test files.")),
    dict(id="coding-ts", kind="coding", lang="ts", src=ROOT / "eval-agent/tasks/ts_bug_store",
         verify="verify.sh", prompt=(
             "There's a bug reported against this repo's VersionedStore: "
             "version numbers used for optimistic-concurrency checks never "
             "advance when a key is updated. Find the bug and fix it. Do not "
             "change test files.")),
]

OXIDE_CMD_RE = re.compile(r"\boxide\s+(context|search)\b")
NATIVE_GREP_RE = re.compile(r"\b(grep|rg|ag)\b")


def sh(cmd, cwd=None, env=None, timeout=300):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)


def build_opencode_config(run_dir, condition):
    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "codegraph": {"type": "local", "command": ["codegraph", "serve", "--mcp"], "enabled": False},
            "codebase-memory-mcp": {"type": "local", "command": ["true"], "enabled": False},
        },
    }
    if condition in ("C", "D", "E"):
        cfg["mcp"]["oxide"] = {
            "type": "local",
            "command": [str(OXIDE_BIN), "mcp"],
            "enabled": True,
        }
    path = run_dir / "opencode-config.json"
    path.write_text(json.dumps(cfg, indent=2))
    return path


def setup_repo(repo_src, condition, run_dir, needs_index):
    repo = run_dir / "repo"
    if repo_src.is_dir() and (repo_src / ".git").exists() is False and repo_src != run_dir:
        shutil.copytree(repo_src, repo, ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
    binpath = run_dir / "bin"
    binpath.mkdir()
    if condition in ("B", "E"):
        os.symlink(OXIDE_BIN, binpath / "oxide")
    if needs_index and condition != "A":
        sh([str(OXIDE_BIN), "index", str(repo), "--json"], timeout=60)
    if condition in ("B", "E"):
        skill_dst = repo / ".opencode/skills/oxide-code-context"
        skill_dst.mkdir(parents=True)
        shutil.copy(SKILL_SRC, skill_dst / "SKILL.md")
    if condition == "B" or condition == "E":
        (repo / "AGENTS.md").write_text(E1_AGENTS_CLI)
    elif condition == "D":
        (repo / "AGENTS.md").write_text(D_AGENTS_MCP)
    return repo, binpath


def build_env(binpath, condition, config_path):
    env = dict(os.environ)
    parts = [p for p in env.get("PATH", "").split(":") if p]
    if condition in ("B", "E"):
        env["PATH"] = f"{binpath}:" + ":".join(parts)
    else:
        parts = [p for p in parts if "target/release" not in p]
        env["PATH"] = ":".join(parts)
    env["OPENCODE_CONFIG"] = str(config_path)
    return env


def parse_events(stdout: str):
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def analyze(events):
    tool_calls = []
    tokens_total = 0
    final_texts = []
    for ev in events:
        part = ev.get("part", {})
        if part.get("type") == "tool":
            tool_calls.append((part.get("tool", ""), part.get("state", {}).get("input", {})))
        elif ev.get("type") == "text":
            final_texts.append(part.get("text", ""))
        elif ev.get("type") == "step_finish":
            tokens_total += (part.get("tokens", {}) or {}).get("total", 0)

    oxide_cli_context = oxide_cli_search = 0
    oxide_mcp_context = oxide_mcp_search = 0
    native_read = native_grep = native_glob = native_list = native_bash_other = 0
    first_action = None
    codegraph_seen = False
    for tool, inp in tool_calls:
        tl = tool.lower()
        if "codegraph" in tl:
            codegraph_seen = True
        if first_action is None:
            cmd = inp.get("command", "") if isinstance(inp, dict) else ""
            if tool == "bash" and OXIDE_CMD_RE.search(cmd):
                first_action = "cli:oxide"
            elif "oxide" in tl and "context" in tl:
                first_action = "mcp:context"
            elif "oxide" in tl and "search" in tl:
                first_action = "mcp:search"
            else:
                first_action = tool
        if tool == "bash":
            cmd = inp.get("command", "") if isinstance(inp, dict) else ""
            oxide_cli_context += len(re.findall(r"\boxide\s+context\b", cmd))
            oxide_cli_search += len(re.findall(r"\boxide\s+search\b", cmd))
            if not OXIDE_CMD_RE.search(cmd):
                if NATIVE_GREP_RE.search(cmd):
                    native_grep += 1
                else:
                    native_bash_other += 1
        elif "oxide" in tl and "context" in tl:
            oxide_mcp_context += 1
        elif "oxide" in tl and "search" in tl:
            oxide_mcp_search += 1
        elif tool == "read":
            native_read += 1
        elif tool == "grep":
            native_grep += 1
        elif tool == "glob":
            native_glob += 1
        elif tool == "list":
            native_list += 1

    used_cli = (oxide_cli_context + oxide_cli_search) > 0
    used_mcp = (oxide_mcp_context + oxide_mcp_search) > 0
    native_explore = native_read + native_grep + native_glob + native_list + native_bash_other
    transport = "NONE"
    if used_cli and used_mcp:
        transport = "BOTH"
    elif used_cli:
        transport = "CLI_ONLY"
    elif used_mcp:
        transport = "MCP_ONLY"
    return dict(
        total_tool_calls=len(tool_calls),
        oxide_cli_context_calls=oxide_cli_context,
        oxide_cli_search_calls=oxide_cli_search,
        oxide_mcp_context_calls=oxide_mcp_context,
        oxide_mcp_search_calls=oxide_mcp_search,
        native_read_calls=native_read,
        native_grep_calls=native_grep,
        native_glob_calls=native_glob,
        native_list_calls=native_list,
        native_bash_other_calls=native_bash_other,
        native_explore_calls=native_explore,
        first_action=first_action,
        used_cli=used_cli,
        used_mcp=used_mcp,
        used_oxide=used_cli or used_mcp,
        transport=transport,
        codegraph_seen=codegraph_seen,
        tokens_total=tokens_total,
        final_text=" ".join(final_texts)[-600:],
        tool_call_sequence=[t for t, _ in tool_calls][:40],
    )


def classify_activation(bucket, analysis, timed_out):
    if timed_out:
        return dict(appropriate=None, missed=None, unnecessary=None)
    used = analysis["used_oxide"]
    missed = bucket == "A" and not used and analysis["native_explore_calls"] >= 2
    unnecessary = bucket == "C" and used
    appropriate = (bucket in ("A", "B") and used) or (bucket == "C" and not used)
    return dict(appropriate=appropriate, missed=missed, unnecessary=unnecessary)


def _invoke(repo, env, prompt, timeout_s):
    start = time.time()
    try:
        r = sh(["opencode", "run", "--format", "json", "--dir", str(repo), "-m", MODEL, *EXTRA_ARGS, prompt],
               cwd=str(repo), env={**env, "PWD": str(repo)}, timeout=timeout_s)
        return r.stdout, r.stderr, r.returncode, False, round(time.time() - start, 1)
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        err = e.stderr or ""
        return (out.decode("utf-8", "replace") if isinstance(out, bytes) else out), \
            (err.decode("utf-8", "replace") if isinstance(err, bytes) else err), -1, True, round(time.time() - start, 1)


def run_nav_one(task, condition, rep):
    run_dir = Path(tempfile.mkdtemp(prefix=f"p31-{task['id']}-{condition}-{rep}-"))
    try:
        config_path = build_opencode_config(run_dir, condition)
        repo, binpath = setup_repo(FIXTURES[task["repo"]], condition, run_dir, needs_index=True)
        env = build_env(binpath, condition, config_path)
        stdout, stderr, rc, timed_out, wall = _invoke(repo, env, task["prompt"], 200)
        if timed_out:
            stdout2, stderr2, rc, timed_out, wall2 = _invoke(repo, env, task["prompt"], 200)
            stdout, stderr, wall = stdout2, stderr2, wall + wall2

        log_name = f"{task['id']}-{condition}-r{rep}.jsonl"
        (LOG_DIR / log_name).write_text(stdout or "")
        if stderr:
            (LOG_DIR / (log_name + ".stderr")).write_text(stderr[-4000:])

        events = parse_events(stdout or "")
        analysis = analyze(events)
        activation = classify_activation(task["bucket"], analysis, timed_out)

        record = dict(
            phase="3.1", kind="nav", task=task["id"], bucket=task["bucket"], condition=condition, rep=rep,
            model=MODEL, wall_s=wall, rc=rc, timed_out=timed_out,
            **analysis, **{f"activation_{k}": v for k, v in activation.items()},
        )
        return record
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def run_coding_one(task, condition, rep):
    run_dir = Path(tempfile.mkdtemp(prefix=f"p31-{task['id']}-{condition}-{rep}-"))
    try:
        config_path = build_opencode_config(run_dir, condition)
        repo, binpath = setup_repo(task["src"], condition, run_dir, needs_index=True)
        env = build_env(binpath, condition, config_path)
        stdout, stderr, rc, timed_out, wall = _invoke(repo, env, task["prompt"], 280)

        log_name = f"{task['id']}-{condition}-r{rep}.jsonl"
        (LOG_DIR / log_name).write_text(stdout or "")
        if stderr:
            (LOG_DIR / (log_name + ".stderr")).write_text(stderr[-4000:])

        events = parse_events(stdout or "")
        analysis = analyze(events)

        verify_ok = False
        verify_rc = None
        if not timed_out:
            vr = sh(["bash", task["verify"]], cwd=str(repo), timeout=120)
            verify_rc = vr.returncode
            verify_ok = vr.returncode == 0

        # preserve diff for patches/
        # Exclude opencode's own runtime deps (written into .opencode/ on
        # first use) and harness-setup files -- an earlier run of this
        # script diffed a full node_modules tree into every patch (884MB
        # for 34 patches; see raw/clean_patches.py for the postmortem fix).
        diff = sh(["diff", "-ruN",
                   "-x", "node_modules", "-x", "__pycache__", "-x", ".oxide",
                   "-x", "bin", "-x", ".git",
                   str(task["src"]), str(repo)], timeout=30).stdout
        (OUT_DIR / "patches" / f"{task['id']}-{condition}-r{rep}.diff").write_text(diff or "")

        outcome = "infrastructure_failure" if timed_out else ("success" if verify_ok else "failure")
        record = dict(
            phase="3.1", kind="coding", task=task["id"], condition=condition, rep=rep,
            model=MODEL, wall_s=wall, rc=rc, timed_out=timed_out, verify_rc=verify_rc, outcome=outcome,
            **analysis,
        )
        return record
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def load_done_keys(path):
    done = set()
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                done.add((r["kind"], r["task"], r["condition"], r["rep"]))
            except Exception:
                continue
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nav-reps", type=int, default=3)
    ap.add_argument("--e-reps", type=int, default=5, help="reps for condition E (redundancy is the phase's core question)")
    ap.add_argument("--coding-reps", type=int, default=3)
    ap.add_argument("--only", nargs="*", default=None, help="restrict to these task ids")
    ap.add_argument("--conditions", nargs="*", default=CONDITIONS)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "patches").mkdir(parents=True, exist_ok=True)
    done = set() if args.force else load_done_keys(RESULTS_PATH)

    with open(RESULTS_PATH, "a") as f:
        for task in NAV_TASKS:
            if args.only and task["id"] not in args.only:
                continue
            for condition in args.conditions:
                reps = args.e_reps if condition == "E" else args.nav_reps
                for rep in range(reps):
                    key = ("nav", task["id"], condition, rep)
                    if key in done:
                        continue
                    t0 = time.time()
                    rec = run_nav_one(task, condition, rep)
                    f.write(json.dumps(rec) + "\n")
                    f.flush()
                    print(f"[nav] {task['id']} {condition} r{rep} -> "
                          f"transport={rec['transport']} used_oxide={rec['used_oxide']} "
                          f"timed_out={rec['timed_out']} ({time.time()-t0:.1f}s)", flush=True)

        for task in CODING_TASKS:
            if args.only and task["id"] not in args.only:
                continue
            for condition in args.conditions:
                reps = args.e_reps if condition == "E" else args.coding_reps
                for rep in range(reps):
                    key = ("coding", task["id"], condition, rep)
                    if key in done:
                        continue
                    t0 = time.time()
                    rec = run_coding_one(task, condition, rep)
                    f.write(json.dumps(rec) + "\n")
                    f.flush()
                    print(f"[coding] {task['id']} {condition} r{rep} -> "
                          f"outcome={rec['outcome']} transport={rec['transport']} "
                          f"({time.time()-t0:.1f}s)", flush=True)


if __name__ == "__main__":
    main()
