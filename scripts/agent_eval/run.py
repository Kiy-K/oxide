#!/usr/bin/env python3
"""OXIDE agent-context evaluation.

Runs the SAME headless coding agent (opencode, fixed model) over identical
tasks under four context conditions and measures outcomes:

  stock      no injected context (agent explores with its own tools)
  vec        top-8 vector-only retrieval injected
  hybrid     top-8 hybrid retrieval injected
  budgeted   budgeted OXIDE context pack (roles, dedup, ordering)

Metrics per cell: solved (verify.sh exit 0), injected context tokens,
wall seconds, shell-tool calls (proxy from transcript), files touched,
unnecessary-edit files, and relevant-symbol recall of the injected context.

Usage: run.py [--oxide PATH] [--model MODEL] [--conditions c1,c2] [--repeat N]
"""
import argparse
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OXIDE = None
MODEL = "opencode/x-preview-f-free"
CHARS_PER_TOKEN = 4.0

TASKS = [
    {
        "id": "py_bug_retry",
        "prompt": (
            "Unit tests in this repository are failing. Find and fix the bug "
            "so that all tests pass. Do not modify anything under tests/."
        ),
        "truth_file": "app/retry.py",
        "truth_symbol": "app/retry.py#RetryPolicy.backoff_ms",
    },
    {
        "id": "py_feat_cache",
        "prompt": (
            "TTLCache.get_or_set is not implemented yet. Implement it so that "
            "all tests pass, honoring its docstring contract exactly. Do not "
            "modify anything under tests/."
        ),
        "truth_file": "storelib/cache.py",
        "truth_symbol": "storelib/cache.py#TTLCache.get_or_set",
    },
    {
        "id": "ts_bug_store",
        "prompt": (
            "Unit tests in this repository are failing. Find and fix the bug "
            "so that all tests pass. Do not modify anything under tests/."
        ),
        "truth_file": "src/versioned_store.ts",
        "truth_symbol": "src/versioned_store.ts#VersionedStore.set",
    },
    {
        "id": "tsx_feat_button",
        "prompt": (
            "The Button component ignores its disabled prop: disabled buttons "
            "must render the disabled attribute and never fire onClick. Fix "
            "the component so all tests pass. Do not modify anything under "
            "tests/."
        ),
        "truth_file": "src/ui/Button.tsx",
        "truth_symbol": "src/ui/Button.tsx#Button",
    },
]

CONDITIONS = ["stock", "vec", "hybrid", "budgeted"]


def est_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def ensure_indexed(fixture_dir: Path, workdir: Path) -> Path:
    """Copy fixture to workdir and index it with the configured embedder."""
    dst = workdir / f"{fixture_dir.name}-idx"
    shutil.copytree(fixture_dir, dst)
    r = sh([str(OXIDE), "index", "."], cwd=dst)
    if r.returncode != 0:
        raise RuntimeError(f"index failed for {fixture_dir.name}: {r.stderr}")
    return dst


def fetch_context(indexed: Path, condition: str, prompt: str):
    """Returns (context_markdown, recall_hit, used_tokens)."""
    truth = next(t["truth_symbol"] for t in TASKS if t["id"] == indexed.name.replace("-idx", ""))
    if condition == "stock":
        return "", False, 0
    if condition == "budgeted":
        r = sh(
            [str(OXIDE), "context", "--task", prompt, "--budget-tokens", "4000", "--json"],
            cwd=indexed,
        )
        data = json.loads(r.stdout)
        parts = []
        hit = False
        for item in data["items"]:
            ident = f"{item['file']}#{item['qualified_name']}"
            if ident == truth:
                hit = True
            header = f"[{item['role']}] `{ident}` ({item['symbol']['kind']}, lines {item['symbol']['start_line']}-{item['symbol']['end_line']}) why: {'; '.join(item['reasons'])}"
            parts.append(f"{header}\n```{item['file'].rsplit('.',1)[-1]}\n{item['snippet']}\n```")
        ctx = "\n\n".join(parts)
        ctx += "\n\n" + data.get("tail_note", "")
        return ctx, hit, est_tokens(ctx)
    # vec / hybrid via search
    mode = "semantic" if condition == "vec" else "hybrid"
    r = sh(
        [str(OXIDE), "search", prompt, "--mode", mode, "--limit", "8", "--json"],
        cwd=indexed,
    )
    hits = json.loads(r.stdout)
    parts = []
    hit = False
    for h in hits:
        ident = f"{h['file']}#{h['qualified_name']}"
        if ident == truth:
            hit = True
        header = f"`{ident}` ({h['kind']}, lines {h['start_line']}-{h['end_line']}) why: {'; '.join(h['reasons'])}"
        lang = h["file"].rsplit(".", 1)[-1]
        parts.append(f"{header}\n```{lang}\n{h['snippet']}\n```")
    return "\n\n".join(parts), hit, est_tokens("\n\n".join(parts))


def build_prompt(task: dict, condition: str, indexed: Path) -> tuple[str, int, bool]:
    body = f"# Task\n\n{task['prompt']}\n\nWhen you believe you are done, stop replying with DONE."
    ctx, hit, toks = fetch_context(indexed, condition, task["prompt"])
    if ctx:
        body += "\n\n# Relevant repository context (pre-retrieved for you)\n\n" + ctx
    return body, toks, hit


def run_agent(repo: Path, prompt_text: str, log: Path) -> tuple[bool, float, int]:
    start = time.time()
    r = sh(["timeout", "600", "opencode", "run", "-m", MODEL, prompt_text], cwd=repo)
    wall = time.time() - start
    log.write_text(r.stdout[-20000:] + ("\n--- STDERR ---\n" + r.stderr[-4000:] if r.stderr else ""))
    tool_calls = r.stdout.count("\n$ ")
    v = sh(["bash", "verify.sh"], cwd=repo)
    return v.returncode == 0, wall, tool_calls


def diff_footprint(pristine: Path, repo: Path):
    r = sh(
        [
            "diff",
            "-rq",
            "--exclude=node_modules",
            "--exclude=.oxide",
            "--exclude=bun.lockb",
            str(pristine),
            str(repo),
        ]
    )
    files = []
    for line in r.stdout.splitlines():
        if line.startswith("Files "):
            # "Files a/x and b/x differ"
            right = line.split(" and ")[1].rsplit(" ", 1)[0]
            files.append(str(Path(right).relative_to(repo)))
    return files


def main() -> None:
    global OXIDE
    ap = argparse.ArgumentParser()
    ap.add_argument("--oxide", default=str(ROOT / "target/release/oxide"))
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--conditions", default=",".join(CONDITIONS))
    ap.add_argument("--tasks", default=",".join(t["id"] for t in TASKS))
    args = ap.parse_args()
    OXIDE = Path(args.oxide)
    MODEL = args.model
    conds = [c for c in args.conditions.split(",") if c]
    want_tasks = set(args.tasks.split(","))

    out_dir = ROOT / "eval-agent/results"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="oxide-agent-eval-"))

    rows = []
    try:
        for task in TASKS:
            if task["id"] not in want_tasks:
                continue
            fixture = ROOT / "eval-agent/tasks" / task["id"]
            indexed = ensure_indexed(fixture, tmp)
            pristine = tmp / task["id"]
            shutil.copytree(fixture, pristine)

            for cond in conds:
                repo = tmp / f'{task["id"]}-{cond}'
                shutil.copytree(fixture, repo)
                prompt_text, ctx_tokens, recall_hit = build_prompt(task, cond, indexed)
                (repo / "_PROMPT.md").write_text(prompt_text)
                ok, wall, tools = run_agent(repo, prompt_text, out_dir / f'{task["id"]}-{cond}.log')
                touched = [f for f in diff_footprint(pristine, repo)]
                bad_edits = [
                    f
                    for f in touched
                    if f != task["truth_file"]
                    and not f.startswith("tests/")
                    and f != "_PROMPT.md"
                ]
                rows.append(
                    {
                        "task": task["id"],
                        "condition": cond,
                        "solved": ok,
                        "ctx_tokens": ctx_tokens,
                        "recall_hit": recall_hit,
                        "wall_s": round(wall, 1),
                        "tool_calls_proxy": tools,
                        "files_touched": len(touched),
                        "unnecessary_edit_files": len(bad_edits),
                        "bad_edit_list": bad_edits,
                    }
                )
                print(json.dumps(rows[-1]))
                shutil.rmtree(repo, ignore_errors=True)
    finally:
        (out_dir / "results.json").write_text(json.dumps(rows, indent=2))
        print(f"\nwrote {out_dir/'results.json'}")
        summary: dict[str, dict] = {}
        for r in rows:
            s = summary.setdefault(r["condition"], {"runs": 0, "solved": 0, "ctx": [], "wall": [], "tools": [], "bad": []})
            s["runs"] += 1
            s["solved"] += int(r["solved"])
            s["ctx"].append(r["ctx_tokens"])
            s["wall"].append(r["wall_s"])
            s["tools"].append(r["tool_calls_proxy"])
            s["bad"].append(r["unnecessary_edit_files"])
        print(f"\n{'condition':<10} {'solve':>6} {'avg_ctx':>8} {'avg_wall':>9} {'avg_tools':>10} {'bad_edits':>10}")
        for cond, s in summary.items():
            n = max(1, s["runs"])
            print(
                f"{cond:<10} {s['solved']}/{s['runs']:>3} {sum(s['ctx'])/n:>8.0f} "
                f"{sum(s['wall'])/n:>8.1f}s {sum(s['tools'])/n:>10.1f} {sum(s['bad']):>10}"
            )


if __name__ == "__main__":
    main()
