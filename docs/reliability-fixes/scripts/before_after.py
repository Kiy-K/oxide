#!/usr/bin/env python3
"""Before/after evidence for the two reliability fixes: a baseline binary
(unfixed commit) vs the fixed binary on the same checkouts.

Per checkout, for each binary (order alternating per repeat): cold
`oxide index` on a fresh copy, timed by docs/scip-rust-eval/scripts/
measure.py (wall + peak RSS of the process tree, one fresh measuring
process per run), N repeats; then on the last index the `symbols` and
`symbol_relations` digests and `context` / hybrid `search` JSON digests.
Hashed embedder: parsing/extraction is what changed, and it keeps runs fast
and deterministic. Also `oxide eval --config fixtures/benchmark.json` from
both binaries: rows compared as a sorted set (values) and raw (order).

usage: before_after.py <baseline oxide> <fixed oxide> <work dir> <out.json> <N> <checkout>...
"""
import hashlib
import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MEASURE = ROOT / "docs/scip-rust-eval/scripts/measure.py"
QUERY = "where is the configuration parsed and validated"


def env():
    e = {k: v for k, v in os.environ.items() if k not in ("OXIDE_EMBED_URL", "OXIDE_SCANNER_POLICY")}
    e["OXIDE_EMBED_NATIVE"] = "hashed"
    return e


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def copy(src, dst):
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-a", "--exclude=/.oxide", str(src) + "/", str(dst) + "/"], check=True)


def timed_index(oxide, repo, log, label):
    subprocess.run([sys.executable, str(MEASURE), label, str(log), "--", oxide, "index", "."],
                   cwd=repo, env=env(), capture_output=True, text=True)
    rec = json.loads(Path(log).read_text().splitlines()[-1])
    if rec["label"] != label:  # measure.py never ran (e.g. bad binary path)
        raise RuntimeError(f"no measurement recorded for {label}")
    return dict(rc=rec["rc"], wall_s=rec["wall_s"], peak_rss_kb=rec["peak_tree_rss_kb"],
                stderr_tail=rec["stderr_tail"][-300:] if rec["rc"] else "")


def digest(oxide, repo):
    con = sqlite3.connect(f"file:{repo / '.oxide/index.db'}?mode=ro", uri=True)
    rows = con.execute("SELECT id, file, qualified_name, kind, start_line, end_line, content_hash, "
                       "signature, imports_json, references_json FROM symbols ORDER BY id").fetchall()
    rels = con.execute("SELECT symbol_id, kind, target FROM symbol_relations "
                       "ORDER BY symbol_id, kind, target").fetchall()
    con.close()
    run = lambda *a: subprocess.run([oxide, *a], cwd=repo, env=env(), capture_output=True,  # noqa: E731
                                    text=True, check=True).stdout
    return dict(symbols=len(rows), symbols_sha=sha(json.dumps(rows)), relations_sha=sha(json.dumps(rels)),
                context_sha=sha(run("context", "--task", QUERY, "--json")),
                search_sha=sha(run("search", QUERY, "--mode", "hybrid", "--limit", "10", "--json")))


def main():
    base, fixed = (str(Path(a).resolve()) for a in sys.argv[1:3])  # measure.py runs inside each copy
    work, out, n = Path(sys.argv[3]), sys.argv[4], int(sys.argv[5])
    work = work.resolve()
    log = Path(out).resolve().with_suffix(".measure.jsonl")
    arms = {"baseline": base, "fixed": fixed}
    res = dict(checkouts={}, eval={})
    for src in sys.argv[6:]:
        name = Path(src).name
        runs = {a: [] for a in arms}
        dig = {}
        for i in range(n):
            for arm in (list(arms) if i % 2 == 0 else list(arms)[::-1]):
                dst = work / name / arm
                copy(src, dst)
                runs[arm].append(timed_index(arms[arm], dst, log, f"{name}-{arm}-{i}"))
                if i == n - 1 and runs[arm][-1]["rc"] == 0:
                    dig[arm] = digest(arms[arm], dst)
        ok = lambda a: [r for r in runs[a] if r["rc"] == 0]  # noqa: E731
        rec = {a: dict(rc=[r["rc"] for r in runs[a]],
                       median_wall_s=statistics.median(r["wall_s"] for r in ok(a)) if ok(a) else None,
                       median_peak_rss_kb=statistics.median(r["peak_rss_kb"] for r in ok(a)) if ok(a) else None,
                       walls=[r["wall_s"] for r in runs[a]],
                       stderr_tail=next((r["stderr_tail"] for r in runs[a] if r["rc"]), ""),
                       digest=dig.get(a)) for a in arms}
        rec["content_identical"] = (dig["baseline"] == dig["fixed"]) if len(dig) == 2 else None
        res["checkouts"][name] = rec
        print(name, {a: (rec[a]["rc"], rec[a]["median_wall_s"], rec[a]["median_peak_rss_kb"]) for a in arms},
              "identical:", rec["content_identical"], flush=True)
    cfg = str(ROOT / "fixtures/benchmark.json")
    ev = {a: subprocess.run([p, "eval", "--config", cfg], env=env(), capture_output=True, text=True,
                            check=True).stdout for a, p in arms.items()}
    res["eval"] = dict(raw_identical=ev["baseline"] == ev["fixed"],
                       sorted_rows_identical=sorted(ev["baseline"].splitlines())
                       == sorted(ev["fixed"].splitlines()),
                       baseline_stdout=ev["baseline"], fixed_stdout=ev["fixed"])
    print("eval sorted rows identical:", res["eval"]["sorted_rows_identical"])
    json.dump(res, open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
