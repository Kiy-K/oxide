#!/usr/bin/env python3
"""Phase 2.2 activation-layer eval driver.

Runs the same headless coding agent (opencode) against a fixed task set,
varying only the instruction layer that tells it OXIDE's CLI exists:

  A - baseline: no oxide binary on PATH, no mention at all
  B - oxide on PATH, one-line mention, no usage guidance
  C - oxide on PATH + skills/oxide-code-context/SKILL.md at .opencode/skills/
  D - oxide on PATH + tiny AGENTS.md rule (no skill)
  E - oxide on PATH + skill + AGENTS.md

No MCP transport exists in this repo; conditions substitute for the phase
brief's MCP-based B-E using the CLI + Skill + AGENTS.md surface that
actually exists. See protocol.md.

Each run is `opencode run --format json --dir <repo> -m <model> <prompt>`,
which streams structured tool_use events (tool name + args) instead of
having to scrape stdout text.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OXIDE_BIN = ROOT / "target/release/oxide"
SKILL_SRC = ROOT / "skills/oxide-code-context/SKILL.md"
MODEL = "opencode/muse-spark-1.2-contributor-free"
OUT_DIR = ROOT / "docs/evals/phase-2.2"
LOG_DIR = OUT_DIR / "logs"
RESULTS_PATH = OUT_DIR / "results.jsonl"

AGENTS_BLOCK = (
    "## OXIDE\n\n"
    "For unfamiliar multi-file coding tasks, use `oxide context` before broad "
    "repository exploration. Use `oxide search` for focused follow-up "
    "discovery. For exact known-file or literal tasks, use normal tools "
    "directly. Read source before editing.\n"
)
BARE_MENTION = (
    "Note: an `oxide` CLI is available on PATH in this repo "
    "(try `oxide --help` if you want to see what it does).\n\n"
)

TASKS = [
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

CONDITIONS = ["A", "B", "C", "D", "E"]

FIXTURES = {"py": ROOT / "fixtures/py_repo", "ts": ROOT / "fixtures/ts_repo"}

OXIDE_CMD_RE = re.compile(r"\boxide\s+(context|search)\b")
NATIVE_GREP_RE = re.compile(r"\b(grep|rg|ag)\b")


def sh(cmd, cwd=None, env=None, timeout=300):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)


def setup_repo(task, condition, run_dir):
    repo = run_dir / "repo"
    shutil.copytree(FIXTURES[task["repo"]], repo)
    binpath = run_dir / "bin"
    binpath.mkdir()
    if condition != "A":
        os.symlink(OXIDE_BIN, binpath / "oxide")
        sh([str(OXIDE_BIN), "index", str(repo), "--json"], timeout=60)
    if condition in ("C", "E"):
        skill_dst = repo / ".opencode/skills/oxide-code-context"
        skill_dst.mkdir(parents=True)
        shutil.copy(SKILL_SRC, skill_dst / "SKILL.md")
    if condition in ("D", "E"):
        (repo / "AGENTS.md").write_text(AGENTS_BLOCK)
    return repo, binpath


def build_env(binpath, condition):
    env = dict(os.environ)
    parts = [p for p in env.get("PATH", "").split(":") if p]
    if condition == "A":
        # Strip any path segment that could resolve a real `oxide` binary.
        parts = [p for p in parts if "target/release" not in p and "oxide_p22" not in p]
        env["PATH"] = ":".join(parts)
    else:
        env["PATH"] = f"{binpath}:" + ":".join(parts)
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
    tool_calls = []  # (tool_name, raw_input)
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

    oxide_context_calls = 0
    oxide_search_calls = 0
    native_read = native_grep = native_glob = native_list = native_bash_other = 0
    first_action = None
    for tool, inp in tool_calls:
        if first_action is None:
            cmd = inp.get("command", "") if isinstance(inp, dict) else ""
            first_action = f"{tool}:oxide" if tool == "bash" and OXIDE_CMD_RE.search(cmd) else tool
        if tool == "bash":
            cmd = inp.get("command", "") if isinstance(inp, dict) else ""
            oxide_context_calls += len(re.findall(r"\boxide\s+context\b", cmd))
            oxide_search_calls += len(re.findall(r"\boxide\s+search\b", cmd))
            if not OXIDE_CMD_RE.search(cmd):
                if NATIVE_GREP_RE.search(cmd):
                    native_grep += 1
                else:
                    native_bash_other += 1
        elif tool == "read":
            native_read += 1
        elif tool == "grep":
            native_grep += 1
        elif tool == "glob":
            native_glob += 1
        elif tool == "list":
            native_list += 1

    used_oxide = (oxide_context_calls + oxide_search_calls) > 0
    native_explore = native_read + native_grep + native_glob + native_list + native_bash_other
    return dict(
        total_tool_calls=len(tool_calls),
        oxide_context_calls=oxide_context_calls,
        oxide_search_calls=oxide_search_calls,
        native_read_calls=native_read,
        native_grep_calls=native_grep,
        native_glob_calls=native_glob,
        native_list_calls=native_list,
        native_bash_other_calls=native_bash_other,
        native_explore_calls=native_explore,
        first_action=first_action,
        used_oxide=used_oxide,
        tokens_total=tokens_total,
        final_text=" ".join(final_texts)[-600:],
    )


def classify_activation(task, analysis, timed_out):
    if timed_out:
        # Infra failure (provider hang/timeout), not an activation data point.
        return dict(appropriate=None, missed=None, unnecessary=None)
    bucket = task["bucket"]
    used = analysis["used_oxide"]
    missed = bucket == "A" and not used and analysis["native_explore_calls"] >= 2
    unnecessary = bucket == "C" and used
    appropriate = (bucket in ("A", "B") and used) or (bucket == "C" and not used)
    return dict(appropriate=appropriate, missed=missed, unnecessary=unnecessary)


def _invoke(repo, env, prompt, timeout_s):
    start = time.time()
    try:
        r = sh(["opencode", "run", "--format", "json", "--dir", str(repo), "-m", MODEL, prompt],
               cwd=str(repo), env={**env, "PWD": str(repo)}, timeout=timeout_s)
        return r.stdout, r.stderr, r.returncode, False, round(time.time() - start, 1)
    except subprocess.TimeoutExpired as e:
        return (e.stdout or ""), (e.stderr or ""), -1, True, round(time.time() - start, 1)


def run_one(task, condition, rep):
    run_dir = Path(tempfile.mkdtemp(prefix=f"p22-{task['id']}-{condition}-{rep}-"))
    try:
        repo, binpath = setup_repo(task, condition, run_dir)
        prompt = task["prompt"]
        if condition == "B":
            prompt = BARE_MENTION + prompt
        env = build_env(binpath, condition)

        stdout, stderr, rc, timed_out, wall = _invoke(repo, env, prompt, 200)
        retried = False
        if timed_out:
            # Free-tier provider is known to hang intermittently under load
            # (see protocol.md "instruction-delivery findings"); one retry
            # separates that from a real infra failure worth recording as such.
            retried = True
            stdout, stderr, rc, timed_out, wall2 = _invoke(repo, env, prompt, 200)
            wall += wall2

        log_name = f"{task['id']}-{condition}-r{rep}.jsonl"
        (LOG_DIR / log_name).write_text(stdout or "")
        if stderr:
            (LOG_DIR / (log_name + ".stderr")).write_text(stderr[-4000:])

        events = parse_events(stdout or "")
        analysis = analyze(events)
        activation = classify_activation(task, analysis, timed_out)

        record = dict(
            task=task["id"], bucket=task["bucket"], repo=task["repo"], condition=condition,
            rep=rep, wall_s=wall, timed_out=timed_out, retried=retried, returncode=rc, log=log_name,
            **analysis, **activation,
        )
        return record
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--conditions", default=",".join(CONDITIONS))
    ap.add_argument("--tasks", default=",".join(t["id"] for t in TASKS))
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    conditions = args.conditions.split(",")
    task_ids = set(args.tasks.split(","))
    tasks = [t for t in TASKS if t["id"] in task_ids]

    done = set()
    if RESULTS_PATH.exists():
        for line in RESULTS_PATH.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done.add((rec["task"], rec["condition"], rec["rep"]))

    jobs = []
    for t in tasks:
        for c in conditions:
            for rep in range(1, args.reps + 1):
                if (t["id"], c, rep) in done:
                    continue
                jobs.append((t, c, rep))

    print(f"{len(jobs)} runs queued ({len(tasks)} tasks x {len(conditions)} conditions x {args.reps} reps, "
          f"{len(done)} already done)")

    with RESULTS_PATH.open("a") as sink, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(run_one, t, c, rep): (t["id"], c, rep) for t, c, rep in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                rec = fut.result()
            except Exception as e:
                print(f"FAIL {key}: {type(e).__name__}: {e}")
                continue
            sink.write(json.dumps(rec) + "\n")
            sink.flush()
            print(f"{rec['task']:<4} {rec['condition']} r{rec['rep']} "
                  f"used_oxide={rec['used_oxide']!s:<5} ctx={rec['oxide_context_calls']} "
                  f"search={rec['oxide_search_calls']} native={rec['native_explore_calls']} "
                  f"first={rec['first_action']!s:<12} wall={rec['wall_s']}s "
                  f"appropriate={rec['appropriate']}")


if __name__ == "__main__":
    main()
