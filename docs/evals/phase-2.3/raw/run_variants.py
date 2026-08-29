#!/usr/bin/env python3
"""Phase 2.3 activation-refinement eval driver.

E0 = Phase 2.2's winning condition (CLI + SKILL.md + tiny AGENTS.md),
taken as the frozen baseline. Every variant below changes ONLY the
AGENTS.md text and/or the skill's frontmatter `description` -- the skill
body, the CLI, retrieval, and everything else stay exactly as shipped.

Same 4 Bucket-A + 4 Bucket-C tasks as Phase 2.2 (reused verbatim, not
redesigned after seeing Phase 2.2's results).
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
MODEL = "openai/gpt-5.6-luna"
OUT_DIR = ROOT / "docs/evals/phase-2.3"
LOG_DIR = OUT_DIR / "logs"
RESULTS_PATH = OUT_DIR / "results.jsonl"

FIXTURES = {"py": ROOT / "fixtures/py_repo", "ts": ROOT / "fixtures/ts_repo"}

# ---- Tasks, reused verbatim from docs/evals/phase-2.2/raw/run_activation_eval.py ----
TASKS = [
    dict(id="A1", bucket="A", repo="py", prompt=(
        "There's a report that our HTTP client sometimes retries requests it "
        "shouldn't (e.g. permanent 4xx client errors), wasting time before "
        "giving up. Find where retry eligibility is decided in this repo and "
        "identify the exact check involved. Report the file and function "
        "name only — do not edit anything.")),
    dict(id="A2", bucket="A", repo="py", prompt=(
        "Some users report getting stale cached data back even though it "
        "should have expired by now. Find where cache expiration is "
        "implemented in this repo and describe how expiry is checked. "
        "Report the file and function only — do not edit anything.")),
    dict(id="A3", bucket="A", repo="ts", prompt=(
        "We refresh an auth token somewhere after it goes stale, but nobody "
        "remembers where that logic lives or what triggers it. Find it and "
        "report the file, the function, and what calls it. Do not edit "
        "anything.")),
    dict(id="A4", bucket="A", repo="ts", prompt=(
        "The API client's retry backoff delay doesn't seem to grow the way "
        "engineers expect for the first couple of retries. Find where the "
        "backoff delay is computed and what implements the retry policy. "
        "Report the file and function only — do not edit anything.")),
    dict(id="C1", bucket="C", repo="py", prompt=(
        "In `oxidepy/cache.py`, rename the `TTLCache` class to `TimedCache`. "
        "Only touch that one file.")),
    dict(id="C2", bucket="C", repo="ts", prompt=(
        "In `src/ui/Button.tsx`, add a one-line comment directly above the "
        "component saying `// TODO: memoize`. Only touch that one file.")),
    dict(id="C3", bucket="C", repo="py", prompt=(
        'In `oxidepy/http_client.py`, add a module-level docstring line at '
        'the very top if one is not already present: `"""Thin HTTP client '
        'wrapper."""`. Only touch that one file.')),
    dict(id="C4", bucket="C", repo="ts", prompt=(
        "In `src/net/retry.ts`, rename the exported const "
        "`defaultRetryPolicy` to `DEFAULT_RETRY_POLICY`. Only touch that one "
        "file.")),
]

# ---- Variants: AGENTS.md text + optional skill description override ----
E0_AGENTS = (
    "## OXIDE\n\n"
    "For unfamiliar multi-file coding tasks, use `oxide context` before broad "
    "repository exploration. Use `oxide search` for focused follow-up "
    "discovery. For exact known-file or literal tasks, use normal tools "
    "directly. Read source before editing.\n"
)
E1_AGENTS = (
    "## OXIDE\n\n"
    "For unfamiliar repository work where the implementation path is not "
    "already known, use `oxide context` before broad grep/read exploration. "
    "Use `oxide search` for focused follow-up discovery. For exact known-file "
    "or literal tasks, use normal tools directly. Read source before "
    "editing.\n"
)
E2_AGENTS = (
    "## OXIDE\n\n"
    "For unfamiliar multi-file coding tasks, before broad repository "
    "exploration run:\n\n"
    '```\noxide context --task "<task>" --json\n```\n\n'
    "For a focused follow-up question, run:\n\n"
    '```\noxide search "<question>" --json\n```\n\n'
    "For exact known-file or literal tasks, use normal tools directly. Read "
    "source before editing.\n"
)
E3_AGENTS = (
    "## OXIDE\n\n"
    "Unknown implementation path -> use `oxide context` before broad "
    "grep/read exploration, then `oxide search` for focused follow-up.\n"
    "Known exact file/literal target -> use normal tools directly.\n"
    "Read source before editing.\n"
)
E4_SKILL_DESCRIPTION = (
    "Use BEFORE grep/read when starting an unfamiliar multi-file task or "
    "localizing an implementation from a bug report or behavior "
    "description — get OXIDE's ranked working set first, then read the "
    "actual files it points to. Skip for known-file, exact-line, or "
    "literal-string tasks."
)

VARIANTS = {
    "E0": dict(agents=E0_AGENTS, skill_description=None),
    "E1": dict(agents=E1_AGENTS, skill_description=None),
    "E2": dict(agents=E2_AGENTS, skill_description=None),
    "E3": dict(agents=E3_AGENTS, skill_description=None),
    "E4": dict(agents=E0_AGENTS, skill_description=E4_SKILL_DESCRIPTION),
}

OXIDE_CMD_RE = re.compile(r"\boxide\s+(context|search)\b")
NATIVE_GREP_RE = re.compile(r"\b(grep|rg|ag)\b")


def sh(cmd, cwd=None, env=None, timeout=300):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)


def build_skill_file(description: str | None) -> str:
    original = SKILL_SRC.read_text()
    if description is None:
        return original
    # Replace the YAML `description: >-` block (frontmatter) only.
    lines = original.splitlines(keepends=True)
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        if line.startswith("description:"):
            i += 1
            while i < len(lines) and lines[i].startswith("  "):
                i += 1  # skip old folded description lines
            out.append(f"  {description}\n")
            continue
        i += 1
    return "".join(out)


def setup_repo(task, variant, run_dir):
    repo = run_dir / "repo"
    shutil.copytree(FIXTURES[task["repo"]], repo)
    binpath = run_dir / "bin"
    binpath.mkdir()
    os.symlink(OXIDE_BIN, binpath / "oxide")
    sh([str(OXIDE_BIN), "index", str(repo), "--json"], timeout=60)

    skill_dst = repo / ".opencode/skills/oxide-code-context"
    skill_dst.mkdir(parents=True)
    (skill_dst / "SKILL.md").write_text(build_skill_file(VARIANTS[variant]["skill_description"]))

    (repo / "AGENTS.md").write_text(VARIANTS[variant]["agents"])
    return repo, binpath


def build_env(binpath):
    env = dict(os.environ)
    parts = [p for p in env.get("PATH", "").split(":") if p]
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
    tool_calls = []
    tokens_total = 0
    final_texts = []
    for ev in events:
        part = ev.get("part", {})
        if part.get("type") == "tool":
            tool_calls.append((part.get("tool", ""), part.get("state", {})))
        elif ev.get("type") == "text":
            final_texts.append(part.get("text", ""))
        elif ev.get("type") == "step_finish":
            tokens_total += (part.get("tokens", {}) or {}).get("total", 0)

    oxide_context_calls = oxide_search_calls = 0
    native_read = native_grep = native_glob = native_list = native_bash_other = 0
    first_action = None
    oxide_first_call_index = None
    for idx, (tool, state) in enumerate(tool_calls):
        inp = state.get("input", {})
        if first_action is None:
            cmd = inp.get("command", "") if isinstance(inp, dict) else ""
            first_action = f"{tool}:oxide" if tool == "bash" and OXIDE_CMD_RE.search(cmd) else tool
        if tool == "bash":
            cmd = inp.get("command", "") if isinstance(inp, dict) else ""
            n_ctx = len(re.findall(r"\boxide\s+context\b", cmd))
            n_srch = len(re.findall(r"\boxide\s+search\b", cmd))
            oxide_context_calls += n_ctx
            oxide_search_calls += n_srch
            if (n_ctx or n_srch) and oxide_first_call_index is None:
                oxide_first_call_index = idx
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
    # "late activation": oxide called, but only after >=2 native exploration calls first
    late_activation = used_oxide and oxide_first_call_index is not None and oxide_first_call_index >= 2
    native_before_oxide = oxide_first_call_index if oxide_first_call_index is not None else native_explore
    is_dead_run = len(tool_calls) == 1 and tool_calls[0][1].get("status") == "error"

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
        first_action_is_oxide=(first_action is not None and "oxide" in first_action),
        used_oxide=used_oxide,
        late_activation=late_activation,
        native_calls_before_oxide=native_before_oxide,
        tokens_total=tokens_total,
        dead_run=is_dead_run,
        final_text=" ".join(final_texts)[-600:],
    )


def classify_activation(task, analysis, timed_out):
    if timed_out or analysis["dead_run"]:
        return dict(appropriate=None, missed=None, unnecessary=None)
    bucket = task["bucket"]
    used = analysis["used_oxide"]
    missed = bucket == "A" and not used
    unnecessary = bucket == "C" and used
    appropriate = (bucket == "A" and used) or (bucket == "C" and not used)
    return dict(appropriate=appropriate, missed=missed, unnecessary=unnecessary)


def _invoke(repo, env, prompt, timeout_s):
    start = time.time()
    try:
        r = sh(["opencode", "run", "--auto", "--format", "json", "--dir", str(repo), "-m", MODEL, prompt],
               cwd=str(repo), env={**env, "PWD": str(repo)}, timeout=timeout_s)
        return r.stdout, r.returncode, False, round(time.time() - start, 1)
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        stdout = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
        return stdout, -1, True, round(time.time() - start, 1)


def run_one(task, variant, rep):
    run_dir = Path(tempfile.mkdtemp(prefix=f"p23-{task['id']}-{variant}-{rep}-"))
    try:
        repo, binpath = setup_repo(task, variant, run_dir)
        env = build_env(binpath)

        stdout, rc, timed_out, wall = _invoke(repo, env, task["prompt"], 200)
        retried = False
        if timed_out:
            retried = True
            stdout2, rc, timed_out, wall2 = _invoke(repo, env, task["prompt"], 200)
            stdout, wall = stdout2, wall + wall2

        log_name = f"{task['id']}-{variant}-r{rep}.jsonl"
        (LOG_DIR / log_name).write_text(stdout or "")

        events = parse_events(stdout or "")
        analysis = analyze(events)
        activation = classify_activation(task, analysis, timed_out)

        record = dict(
            task=task["id"], bucket=task["bucket"], repo=task["repo"], variant=variant,
            rep=rep, wall_s=wall, timed_out=timed_out, retried=retried, returncode=rc, log=log_name,
            **analysis, **activation,
        )
        return record
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--variants", default=",".join(VARIANTS.keys()))
    ap.add_argument("--tasks", default=",".join(t["id"] for t in TASKS))
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    variants = args.variants.split(",")
    task_ids = set(args.tasks.split(","))
    tasks = [t for t in TASKS if t["id"] in task_ids]

    done = set()
    if RESULTS_PATH.exists():
        for line in RESULTS_PATH.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done.add((rec["task"], rec["variant"], rec["rep"]))

    jobs = []
    for t in tasks:
        for v in variants:
            for rep in range(1, args.reps + 1):
                if (t["id"], v, rep) in done:
                    continue
                jobs.append((t, v, rep))

    print(f"{len(jobs)} runs queued ({len(tasks)} tasks x {len(variants)} variants x {args.reps} reps, "
          f"{len(done)} already done)")

    with RESULTS_PATH.open("a") as sink, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(run_one, t, v, rep): (t["id"], v, rep) for t, v, rep in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                rec = fut.result()
            except Exception as e:
                print(f"FAIL {key}: {type(e).__name__}: {e}")
                continue
            sink.write(json.dumps(rec) + "\n")
            sink.flush()
            print(f"{rec['task']:<4} {rec['variant']:<3} r{rec['rep']} "
                  f"used_oxide={rec['used_oxide']!s:<5} dead={rec['dead_run']!s:<5} "
                  f"ctx={rec['oxide_context_calls']} search={rec['oxide_search_calls']} "
                  f"native={rec['native_explore_calls']} first={rec['first_action']!s:<12} "
                  f"wall={rec['wall_s']}s appropriate={rec['appropriate']}")


if __name__ == "__main__":
    main()
