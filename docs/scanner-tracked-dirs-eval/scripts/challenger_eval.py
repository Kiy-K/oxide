#!/usr/bin/env python3
"""Per affected ContextBench instance x scanner policy: cold index cost,
what got indexed, retrieval quality (corrected ContextBench scorer),
request latency, and a single-file incremental update.

Arms: `main` / `tracked` / `narrow` = the challenger binary
(../challenger.patch) with OXIDE_SCANNER_POLICY unset / set. Every arm
indexes its own rsync copy of the commit-keyed checkout (without `.oxide/`)
under <work>/<tag>/<arm>, with the shipped default embedder. The
incremental probe appends a trailing space to the middle line of the
largest non-module symbol in one file (a gold file under a skipped
directory when the arm indexed one, else the largest symbol in the repo),
re-indexes, and restores the file from memory — no git writes (the copies
share their worktree's git index).

usage: challenger_eval.py <tasks.jsonl> <challenger oxide> <work dir> <out.jsonl>
           [--only TAG] [--arms main,tracked,narrow] [--skip-equal]

`main` is the challenger binary with OXIDE_SCANNER_POLICY unset (its parity
with the real main binary is checked separately by parity.py). Arm order
rotates per task. With --skip-equal an arm whose offline-screened scan set
equals `main`'s or `tracked`'s is recorded as `identical_to` instead of
being re-indexed.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/agent_eval"))
sys.path.insert(0, str(HERE))
import contextbench_run as cb  # noqa: E402
import policy_screen as ps  # noqa: E402
from affected import denied_dirs  # noqa: E402

MEASURE = ROOT / "docs/scip-rust-eval/scripts/measure.py"
REPEATS = 5


def env_for(arm):
    env = {k: v for k, v in os.environ.items()
           if k not in ("OXIDE_EMBED_URL", "OXIDE_EMBED_NATIVE", "OXIDE_SCANNER_POLICY")}
    if arm in ("tracked", "narrow"):
        env["OXIDE_SCANNER_POLICY"] = arm
    return env


def measured(label, out_jsonl, cmd, cwd, env):
    p = subprocess.run([sys.executable, str(MEASURE), label, str(out_jsonl), "--", *cmd],
                       cwd=cwd, env=env, capture_output=True, text=True, timeout=7200)
    if p.returncode:
        raise RuntimeError(f"{label}: {p.stderr[-400:]}")
    rec = json.loads(Path(out_jsonl).read_text().splitlines()[-1])
    return {k: rec[k] for k in ("wall_s", "max_single_rss_kb", "peak_tree_rss_kb", "user_s")}


def db_stats(repo):
    db = repo / ".oxide" / "index.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    files = [f for (f,) in con.execute("SELECT DISTINCT file FROM symbols")]
    n_sym = con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    n_emb = con.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    con.close()
    o = repo / ".oxide"
    size = lambda n: (o / n).stat().st_size if (o / n).exists() else 0  # noqa: E731
    new = sorted(f for f in files if denied_dirs(f))
    return dict(files=len(files), symbols=n_sym, embeddings=n_emb, db_bytes=size("index.db"),
                wal_bytes=size("index.db-wal"), files_under_skipped_dirs=new)


def timed_json(cmd, cwd, env):
    walls, out = [], None
    for _ in range(REPEATS):
        t = time.perf_counter()
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
        walls.append(time.perf_counter() - t)
        if p.returncode:
            raise RuntimeError(p.stdout[-300:] + p.stderr[-300:])
        cur = json.loads(p.stdout)
        if out is not None and json.dumps(cur, sort_keys=True) != json.dumps(out, sort_keys=True):
            raise RuntimeError("nondeterministic output across repeats")
        out = cur
    return out, dict(median_s=round(statistics.median(walls), 4), min_s=round(min(walls), 4))


def probe_targets(src, aff):
    """Same (file, line) for every arm, chosen from the main-policy view:
    `skipped` = the first gold file under a skipped directory with an
    indexable extension (middle line of the file), `regular` = the middle
    line of the largest non-module symbol outside any skipped directory,
    read from the first arm's freshly built index (identical for every arm
    outside skipped directories)."""
    out = {}
    for g in aff["gold_in_denied_dirs"]:
        p = Path(aff["path"]) / g
        if p.is_file() and g.rsplit(".", 1)[-1] in ("rs", "go", "py", "js", "ts", "c", "h"):
            out["skipped"] = (g, max(1, p.read_bytes().count(b"\n") // 2))
            break
    con = sqlite3.connect(f"file:{src / '.oxide' / 'index.db'}?mode=ro", uri=True)
    for f, s, e in con.execute("SELECT file, start_line, end_line FROM symbols WHERE kind<>'module' "
                               "ORDER BY (end_line-start_line) DESC, file, start_line"):
        if not denied_dirs(f):
            out["regular"] = (f, (s + e) // 2)
            break
    con.close()
    return out


def incremental(repo, oxide, env, out_jsonl, label, target, repeats=3):
    """Edit -> `oxide index` -> restore -> `oxide index`, `repeats` times;
    reports the median edit re-index (single runs vary by ~0.1 s)."""
    f, line = target
    path = repo / f
    original = path.read_bytes()
    lines = original.split(b"\n")
    mid = line - 1
    lines[mid] = lines[mid] + b" "
    edited = b"\n".join(lines)
    runs = []
    for i in range(repeats):
        path.write_bytes(edited)
        try:
            runs.append(measured(f"{label}-{i}", out_jsonl, [oxide, "index", "."], repo, env))
        finally:
            path.write_bytes(original)
        measured(f"{label}-{i}-revert", out_jsonl, [oxide, "index", "."], repo, env)
    med = sorted(runs, key=lambda r: r["wall_s"])[len(runs) // 2]
    return dict(file=f, line=line, runs=[r["wall_s"] for r in runs], **med)


def score(repo, row, items):
    m = cb.evaluate_task(repo, row, items)
    return {g: {k: m[g][k] for k in ("coverage", "precision", "intersection", "gold_size")}
            for g in ("file", "symbol", "span", "line")}


def copy_checkout(src, dst):
    """rsync copy without the checkout's own `.oxide/` (anchored: an
    unanchored pattern would also drop tracked directories of that name at
    any depth), then fail loudly unless every tracked file made it."""
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-a", "--exclude=/.oxide", src + "/", str(dst) + "/"], check=True)
    tracked = subprocess.run(["git", "-C", src, "ls-files", "-z"], capture_output=True,
                             check=True).stdout.decode("utf-8", "surrogateescape").split("\0")
    missing = [t for t in tracked if t and not os.path.lexists(dst / t)]
    if missing:
        raise RuntimeError(f"copy lost {len(missing)} tracked files, e.g. {missing[:3]}")


def main():
    tasks_path, chal_bin, work, out = sys.argv[1:5]
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    arms_all = (sys.argv[sys.argv.index("--arms") + 1].split(",") if "--arms" in sys.argv
                else ["main", "tracked", "narrow"])
    skip_equal = "--skip-equal" in sys.argv
    tasks = [json.loads(l) for l in open(tasks_path)]
    rows = {r["instance_id"]: r for r in cb.load_tasks(langs=("python", "typescript", "javascript",
                                                                "go", "rust", "c", "cpp", "java"))}
    missing = [t["instance_id"] for t in tasks if t["instance_id"] not in rows]
    assert not missing, f"instances not in load_tasks(): {missing}"
    work = Path(work).resolve()
    out = str(Path(out).resolve())
    raw = Path(out).with_suffix(".measure.jsonl")  # absolute: measure.py runs inside each copy
    for n, task in enumerate(tasks):
        tag = task["instance_id"][-8:]
        if only and tag != only:
            continue
        row = rows[task["instance_id"]]
        screen = ps.screen(task["path"])
        adds = {"main": [], "tracked": screen["tracked"]["index_paths"],
                "narrow": screen["narrow"]["index_paths"]}
        k = n % len(arms_all)
        arms = arms_all[k:] + arms_all[:k]  # rotate so no arm always pays first-touch cost
        targets = None
        for arm in arms:
            rec = dict(instance_id=task["instance_id"], repo=task["repo"], arm=arm,
                       gold_in_denied_dirs=task.get("gold_in_denied_dirs", []),
                       screened_additions=adds[arm])
            if skip_equal and arm != "main":
                twin = next((a for a in arms_all if a != arm and a != "narrow" and adds[a] == adds[arm]), None)
                if twin:
                    rec["identical_to"] = twin  # scan set provably equal: not re-indexed
                    with open(out, "a") as fh:
                        fh.write(json.dumps(rec) + "\n")
                    print(tag, arm, "= ", twin, flush=True)
                    continue
            env = env_for(arm)
            dst = work / tag / arm
            try:
                copy_checkout(task["path"], dst)
                rec["cold_index"] = measured(f"{tag}-{arm}-cold", raw, [chal_bin, "index", "."], dst, env)
                rec["index"] = db_stats(dst)
                if targets is None:  # the first arm's index picks the probes for every arm
                    targets = probe_targets(dst, task)
                rec["probe_targets"] = targets
                pack, rec["context_latency"] = timed_json([chal_bin, "context", "--task",
                                                           row["problem_statement"], "--json"], dst, env)
                hits, rec["search_latency"] = timed_json([chal_bin, "search", row["problem_statement"],
                                                          "--mode", "hybrid", "--limit", "10", "--json"],
                                                         dst, env)
                items = [{"file": i["file"], "start_line": i["start_line"], "end_line": i["end_line"]}
                         for i in pack["items"]]
                hit_items = [{"file": h["file"], "start_line": h["start_line"], "end_line": h["end_line"]}
                             for h in hits]
                rec["context"] = dict(score=score(dst, row, items), used_tokens=pack["used_tokens"],
                                      items=[(i["id"], i["kind"], i["start_line"], i["end_line"], i["reasons"])
                                             for i in pack["items"]],
                                      sha256=hashlib.sha256(json.dumps(pack, sort_keys=True).encode()).hexdigest())
                rec["search"] = dict(score=score(dst, row, hit_items),
                                     hits=[(f'{h["file"]}#{h["qualified_name"]}', h["start_line"], h["end_line"])
                                           for h in hits],
                                     sha256=hashlib.sha256(json.dumps(hits, sort_keys=True).encode()).hexdigest())
                for kind, target in targets.items():
                    rec[f"incremental_{kind}"] = incremental(dst, chal_bin, env, raw,
                                                             f"{tag}-{arm}-incr-{kind}", target)
            except Exception as e:  # recorded, never skipped silently
                rec["error"] = f"{type(e).__name__}: {e}"[:800]
            with open(out, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
            print(tag, arm, rec.get("error") or (rec["index"]["files"], len(rec["index"]["files_under_skipped_dirs"]),
                                                  rec["context"]["score"]["file"]["coverage"]), flush=True)


if __name__ == "__main__":
    main()
