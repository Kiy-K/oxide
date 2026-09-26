#!/usr/bin/env python3
"""Determinism/parity checks for the scanner challenger (hashed embedder:
only the scanner is under test, and it is fast and deterministic).

(a) challenger with OXIDE_SCANNER_POLICY unset vs the real main binary:
    `symbols` rows and `context` / hybrid `search` JSON, byte-identical, on
    the given checkouts;
(b) `oxide eval --config fixtures/benchmark.json`: main binary (twice) vs
    challenger under unset / tracked / narrow — rows identical as a sorted
    set (row order is process-dependent, see below).

usage: parity.py <main oxide> <challenger oxide> <work dir> <out.json> <checkout>...
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from challenger_eval import copy_checkout

ROOT = Path(__file__).resolve().parents[3]
QUERY = "where is the configuration parsed and validated"


def env(policy=None):
    e = {k: v for k, v in os.environ.items()
         if k not in ("OXIDE_EMBED_URL", "OXIDE_SCANNER_POLICY")}
    e["OXIDE_EMBED_NATIVE"] = "hashed"
    if policy:
        e["OXIDE_SCANNER_POLICY"] = policy
    return e


def run(cmd, cwd, e):
    p = subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True, timeout=3600)
    if p.returncode:
        raise RuntimeError(f"{cmd[:2]}: {p.stderr[-300:]}")
    return p.stdout


def digest(repo, oxide, e):
    run([oxide, "index", "."], repo, e)
    con = sqlite3.connect(f"file:{repo / '.oxide/index.db'}?mode=ro", uri=True)
    rows = con.execute("SELECT id, file, qualified_name, kind, start_line, end_line, content_hash, "
                       "references_json FROM symbols ORDER BY id").fetchall()
    con.close()
    h = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731
    return dict(symbols=len(rows), symbols_sha=h(json.dumps(rows)),
                context_sha=h(run([oxide, "context", "--task", QUERY, "--json"], repo, e)),
                search_sha=h(run([oxide, "search", QUERY, "--mode", "hybrid", "--limit", "10",
                                  "--json"], repo, e)))


def main():
    main_bin, chal_bin, work, out = sys.argv[1:5]
    res = dict(checkouts={}, eval={})
    for src in sys.argv[5:]:
        name = Path(src).name
        a, b = Path(work) / name / "main-binary", Path(work) / name / "challenger-unset"
        copy_checkout(src, a)
        copy_checkout(src, b)
        da, db = digest(a, main_bin, env()), digest(b, chal_bin, env())
        res["checkouts"][name] = dict(main_binary=da, challenger_unset=db, identical=da == db)
        print(name, da == db, flush=True)
    # `oxide eval` iterates `config.repos`, a std HashMap (src/eval.rs:14,
    # :113), so its per-repo row *order* varies between processes of the
    # same binary; the main binary runs twice to show that, and rows are
    # compared both raw and sorted.
    cfg = str(ROOT / "fixtures/benchmark.json")
    outs = {"main-binary": run([main_bin, "eval", "--config", cfg], ROOT, env()),
            "main-binary-rerun": run([main_bin, "eval", "--config", cfg], ROOT, env())}
    for pol in (None, "tracked", "narrow"):
        outs[f"challenger-{pol or 'unset'}"] = run([chal_bin, "eval", "--config", cfg], ROOT, env(pol))
    ref = outs["main-binary"]
    rows = lambda v: sorted(v.splitlines())  # noqa: E731
    res["eval"] = {k: dict(raw_identical=v == ref, sorted_rows_identical=rows(v) == rows(ref),
                           sorted_sha256=hashlib.sha256("\n".join(rows(v)).encode()).hexdigest())
                   for k, v in outs.items()}
    print(json.dumps(res["eval"], indent=1))
    json.dump(res, open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
