#!/usr/bin/env python3
"""Measure OXIDE indexing/query costs and stale-index behavior on py_repo."""
import json
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OX = ROOT / "target/release/oxide"
FIXTURE = ROOT / "fixtures/py_repo"
ENV = {
    **os.environ,
    "OXIDE_EMBED_URL": "http://127.0.0.1:8191/v1/embeddings",
    "OXIDE_EMBED_MODEL": "qwen3-Q8_0",
}


def timed(cmd, cwd, capture=False):
    wrapped = ["/usr/bin/time", "-f", "%e %M", *map(str, cmd)]
    started = time.perf_counter()
    p = subprocess.run(wrapped, cwd=cwd, env=ENV, text=True,
                       capture_output=capture)
    elapsed = time.perf_counter() - started
    wall_s = None
    rss_kb = None
    if capture and p.stderr:
        for line in reversed(p.stderr.strip().splitlines()):
            m = re.match(r"^\s*(\d+(?:\.\d+)?)\s+(\d+)\s*$", line)
            if m:
                wall_s = float(m.group(1))
                rss_kb = int(m.group(2))
                break
    return elapsed, (wall_s, rss_kb), p


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, env=ENV, text=True,
                          capture_output=True, check=True)


def search_names(repo, query):
    out = run([OX, "search", query, "--mode", "lexical", "--limit", "10", "--json"], repo)
    return [x["qualified_name"] for x in json.loads(out.stdout)]


def main():
    with tempfile.TemporaryDirectory(prefix="oxide-systems-") as td:
        root = Path(td)
        cold = root / "cold"
        shutil.copytree(FIXTURE, cold, ignore=shutil.ignore_patterns(".oxide"))
        cold_t, cold_rm, _ = timed([OX, "index", "."], cold, capture=True)
        steady = [timed([OX, "index", "."], cold, capture=True)[0] for _ in range(3)]
        (cold / "oxidepy" / "auth.py").open("a").write("\ndef systems_cost_helper():\n    return 1\n")
        edit_t, edit_rm, _ = timed([OX, "index", "."], cold, capture=True)
        search_times = []
        context_times = []
        for _ in range(8):
            started = time.perf_counter()
            run([OX, "search", "retry backoff when token expires", "--mode", "hybrid", "--limit", "10", "--json"], cold)
            search_times.append(time.perf_counter() - started)
        for _ in range(5):
            started = time.perf_counter()
            run([OX, "context", "--task", "retry backoff when token expires", "--budget-tokens", "4096", "--json"], cold)
            context_times.append(time.perf_counter() - started)
        print("systems_cost")
        print(f"cold_index_s={cold_t:.3f} peak_rss_kb={cold_rm[1] if cold_rm[1] is not None else 'n/a'}")
        print(f"no_change_reindex_s_median={statistics.median(steady):.3f} samples=" + ",".join(f"{x:.3f}" for x in steady))
        print(f"single_edit_reindex_s={edit_t:.3f} peak_rss_kb={edit_rm[1] if edit_rm[1] is not None else 'n/a'}")
        print(f"search_hybrid_s_median={statistics.median(search_times):.3f} samples=" + ",".join(f"{x:.3f}" for x in search_times))
        print(f"context_budgeted_s_median={statistics.median(context_times):.3f} samples=" + ",".join(f"{x:.3f}" for x in context_times))

        stale = root / "stale"
        shutil.copytree(FIXTURE, stale, ignore=shutil.ignore_patterns(".oxide"))
        run([OX, "index", "."], stale)
        auth = stale / "oxidepy" / "auth.py"
        source = auth.read_text()
        auth.write_text(source.replace("def decode_claims", "def decode_claims_renamed", 1))
        stale_old = search_names(stale, "decode_claims")
        run([OX, "index", "."], stale)
        fresh_new = search_names(stale, "decode_claims_renamed")
        fresh_old = [n for n in search_names(stale, "decode_claims token claims") if n == "decode_claims"]
        print("stale_index")
        print(f"before_reindex_old_name_present={('decode_claims' in stale_old)}")
        print(f"after_reindex_new_name_present={('decode_claims_renamed' in fresh_new)}")
        print(f"after_reindex_old_name_present={bool(fresh_old)}")


if __name__ == "__main__":
    main()
