#!/usr/bin/env python3
"""Phase 2.1 runner: OpenCode A/B/C across pinned tasks, isolated configs."""
import json, os, shutil, subprocess, tempfile, time, sys, pathlib, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures"
TASKS = [
    ("A1", "A", "py_repo", "Fix RetryPolicy.should_retry so it does not retry on 4xx — only 5xx (status_code >=500) and transient ConnectionError/TimeoutError, with attempt>=max_attempts guard. Locate the implementation, then fix and confirm the logic.", "py_repo"),
    ("A2", "A", "py_repo", "Locate AuthService.refresh_token, its token-store dependency, and its tests; explain whether a missing stored token is rejected before refresh.", "py_repo"),
    ("A3", "A", "ts_repo", "Locate VersionedStore TTL expiry handling and ensure expired entries are evicted on read. Report the file and logic.", "ts_repo"),
    ("A4", "A", "oxide", "Locate where the parser handles Python decorator spans (decorator range included). Report the file and lines.", "oxide"),
    ("A5", "A", "seaborn", "Locate where categorical plotting determines order and handles missing categories across the plotting implementation and tests. Report relevant files and symbols.", "seaborn"),
    ("A6", "A", "deepseek", "Locate the TypeScript configuration/inheritance path that resolves model settings across a parent and child configuration. Report implementation and test files.", "deepseek"),
    ("B1", "B", "py_repo", "In oxidepy/cache.py subsystem, locate TTLCache expiry handling and report the file/logic. Package is oxidepy/cache, find the relevant symbol.", "py_repo"),
    ("B2", "B", "ts_repo", "In src/net/ subsystem, locate retry/backoff implementation. Report the file and logic.", "ts_repo"),
    ("B3", "B", "oxide", "In the review subsystem, locate diff → changed symbols → related context logic. Report the file.", "oxide"),
    ("C1", "C", "py_repo", "In oxidepy/retry.py line 22, rename param base_delay_ms to initial_delay_ms and update its uses in that file only. This is an exact-file edit.", "py_repo"),
    ("C2", "C", "ts_repo", "With known path src/ui/Button.tsx, read that file and report the exported Button component. Do not search broadly.", "ts_repo"),
    ("C3", "C", "oxide", "In src/mcp.rs at the SERVER_INSTRUCTIONS constant, the task is a trivial known-file read of that constant — just report it.", "oxide"),
]

PROMPT_TMPL = "{task}\nDo not edit files unless the task requires a fix; if navigation-only, report file:line and snippet. For edits, make the minimal fix in place."

CONFIGS = {
    "A": "/tmp/oxide-opencode-none.json",  # no oxide
    "B": "/tmp/oxide-opencode-b.json",     # oxide without instructions
    "C": "/tmp/oxide-opencode.json",       # oxide with compact instructions
}
# Ensure configs exist with codegraph disabled
for p in CONFIGS.values():
    if not Path(p).exists():
        print(f"missing config {p}", file=sys.stderr); sys.exit(2)

def sh(cmd, cwd=None, env=None, timeout=120):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env or {})})

def copy_repo(kind, dst):
    dst = Path(dst)
    if kind == "py_repo":
        shutil.copytree(FIXTURES/"py_repo", dst, dirs_exist_ok=True)
    elif kind == "ts_repo":
        shutil.copytree(FIXTURES/"ts_repo", dst, dirs_exist_ok=True)
    elif kind == "oxide":
        for p in ["src","fixtures","Cargo.toml","Cargo.lock"]:
            s = ROOT/p
            d = dst/p
            if s.is_dir(): shutil.copytree(s, d, dirs_exist_ok=True)
            else: shutil.copy2(s, d)
    elif kind in {"seaborn", "deepseek"}:
        s = Path("/home/khoi/Projects/seaborn" if kind == "seaborn" else "/home/khoi/Projects/deepseek-harness")
        expected = {"seaborn": "f04b6cd5484267a0885d1fed068e99dff3a1b226", "deepseek": "99f6f02fecdb7dff40c3fbc9470f5907c29f74ca"}[kind]
        actual = subprocess.run(["git", "-C", str(s), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        if actual != expected:
            raise RuntimeError(f"{kind} changed: expected {expected}, got {actual}")
        shutil.copytree(s, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
    else:
        raise ValueError(kind)

def index_repo(path):
    r = sh([str(ROOT/"target/debug/oxide"), "index", str(path)], timeout=60)
    return r.returncode, r.stdout[:500], r.stderr[:500]
def run_opencode(task_prompt, repo_dir, config_path, model="opencode/nemotron-3-ultra-free", timeout=90):
    env = {"OPENCODE_CONFIG": config_path}
    cmd = ["opencode", "run", "--pure", "--model", model, "--format", "json", "--dir", str(repo_dir), task_prompt]
    start = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env={**os.environ, **env})
        timed_out = False
    except subprocess.TimeoutExpired as e:
        r = subprocess.CompletedProcess(cmd, 124, e.stdout or "", e.stderr or "")
        timed_out = True
    wall = time.time() - start
    # Parse json lines
    calls = []
    tokens = None
    raw = (r.stdout.decode(errors="replace") if isinstance(r.stdout, bytes) else (r.stdout or "")) + (r.stderr.decode(errors="replace") if isinstance(r.stderr, bytes) else (r.stderr or ""))
    for line in raw.splitlines():
        line=line.strip()
        if not line.startswith("{"): continue
        try:
            j=json.loads(line)
        except: continue
        # record tool uses
        if j.get("type")=="tool_use":
            tool = j.get("part",{}).get("tool","")
            inp = j.get("part",{}).get("state",{}).get("input",{})
            calls.append({"tool":tool, "input":inp})
        if j.get("type")=="step_finish" and "tokens" in j.get("part",{}):
            tokens=j["part"]["tokens"]
    return {"wall":wall, "calls":calls, "raw":raw, "tokens":tokens, "rc":r.returncode, "timed_out":timed_out}

def classify_calls(calls):
    oxide_context = sum(1 for c in calls if "oxide_context" in c["tool"])
    oxide_search = sum(1 for c in calls if "oxide_search" in c["tool"] or "oxide_search" in str(c))
    # native search: grep, ripgrep, glob, bash grep
    native_search = sum(1 for c in calls if any(k in c["tool"] for k in ["grep","search","glob","bash"]) or ("bash" in c["tool"] and "grep" in str(c["input"])))
    reads = sum(1 for c in calls if "read" in c["tool"].lower())
    total = len(calls)
    return {"context":oxide_context, "search":oxide_search, "native_search":native_search, "reads":reads, "total":total}

def main():
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--tasks", nargs="*", default=None)
    ap.add_argument("--out", default="docs/evals/phase-2.1/raw")
    ap.add_argument("--summary", default="docs/evals/phase-2.1/results.jsonl")
    ap.add_argument("--model", default="opencode/ling-3.0-flash-fin-free")
    ap.add_argument("--timeout", type=int, default=60)
    args=ap.parse_args()
    sel = set(args.tasks) if args.tasks else None
    outdir=Path(args.out); outdir.mkdir(parents=True, exist_ok=True)
    summary=Path(args.summary); summary.parent.mkdir(parents=True, exist_ok=True)
    # clear summary
    if summary.exists(): summary.unlink()
    for tid, bucket, repo_kind, prompt, _ in TASKS:
        if sel and tid not in sel: continue
        for cond in ["A","B","C"]:
            for rep in range(args.reps):
                with tempfile.TemporaryDirectory() as td:
                    repo_dir = Path(td)/"repo"
                    copy_repo(repo_kind, repo_dir)
                    rc, out, err = index_repo(repo_dir)
                    if rc!=0:
                        print(f"index fail {tid} {repo_kind} {out} {err}")
                    task_prompt = PROMPT_TMPL.format(task=prompt)
                    res = run_opencode(task_prompt, repo_dir, CONFIGS[cond], model=args.model, timeout=args.timeout)
                    cls = classify_calls(res["calls"])
                    rec = {"task":tid,"bucket":bucket,"repo":repo_kind,"condition":cond,"rep":rep,"repo_path":str(repo_dir),"wall":res["wall"],"status":"timeout" if res["timed_out"] else ("ok" if res["rc"] == 0 else "infrastructure_failure"),"calls":cls,"tools":res["calls"][:10],"tokens":res["tokens"]}
                    # write per-run log
                    fname = outdir / f"{tid}_{cond}_r{rep}.json"
                    with open(fname,"w") as f:
                        json.dump(rec, f, indent=2)
                        f.write("\n---raw---\n")
                        f.write(res["raw"][:8000])
                    # append jsonl
                    with open(summary,"a") as f:
                        json.dump(rec, f); f.write("\n")
                    print(f"{tid} {cond} r{rep}: ctx={cls['context']} search={cls['search']} native={cls['native_search']} reads={cls['reads']} total={cls['total']} wall={res['wall']:.1f}s")
                    time.sleep(0.5)

if __name__=="__main__":
    main()
