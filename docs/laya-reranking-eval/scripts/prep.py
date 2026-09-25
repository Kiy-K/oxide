#!/usr/bin/env python3
"""Build Laya shortlist inputs for issue #15 (stdlib only).

  prep.py cb  <baseline_dir> <out.jsonl>
      ContextBench: every candidate of the captured `kept` pool (<= 17, so the
      N=20 shortlist is the whole pool), in production allocation order.
      Source = production's render_snippet window at the 350-token item cap,
      read from the repo checked out at its base commit.
  prep.py dev <regime: plain|masked> <out.jsonl>
      Dev: fused top-20 from the frozen ranking-fusion-eval dump. Source is
      rebuilt from `git show HEAD:<file>` of the cached clone whose HEAD
      reproduces the dump's spans (protocol §3); zod is excluded (no clone).

Only the query text and candidate code go into the output: no gold, no
commit diff, no labels.
"""
import gzip
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oxide_replay as R  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
RFE = ROOT / "docs/ranking-fusion-eval/results"
CLONES = Path.home() / ".cache/oxide-contextbench/repos"
DEV_REPOS = {"pylint", "pytest", "flask", "requests"}
N = 20


def cand(s, source):
    path, qname = s.split("#", 1)
    return {"sid": s, "path": path, "symbol": qname, "source": source}


def prep_cb(base, out):
    tasks = [json.loads(l) for l in open(RFE / "cb-tasks.jsonl")]
    with open(out, "w") as fh:
        for t in tasks:
            kept = json.load(open(Path(base) / "kept" / f"{t['id']}.json"))
            ids = R.ids_for(kept)
            order = R.alloc_order(kept, ids)[:N]
            terms = R.query_terms(t["query"])
            cands = [cand(R.sid(c), R.render_snippet(t["path"], c["symbol"], terms, R.PER_ITEM_CAP))
                     for c in order]
            fh.write(json.dumps({"id": t["id"], "query": t["query"], "cands": cands}) + "\n")


_GIT = {}


def git_lines(repo, rel):
    k = (repo, rel)
    if k not in _GIT:
        r = subprocess.run(["git", "-C", str(CLONES / repo), "show", f"HEAD:{rel}"], capture_output=True)
        try:
            _GIT[k] = R.rust_lines(r.stdout.decode("utf-8")) if r.returncode == 0 else None
        except UnicodeDecodeError:
            _GIT[k] = None
    return _GIT[k]


def prep_dev(regime, out):
    tfile = "tasks.jsonl" if regime == "plain" else "tasks-masked.jsonl"
    tasks = {json.loads(l)["id"]: json.loads(l) for l in open(RFE / tfile)}
    missing = 0
    with open(out, "w") as fh:
        for l in gzip.open(RFE / f"dump-{regime}.jsonl.gz", "rt"):
            d = json.loads(l)
            t = tasks[d["id"]]
            if t["repo"] not in DEV_REPOS:
                continue
            terms = R.query_terms(t["query"])
            cands = []
            for row in d["fused"][:N]:
                s = row[0]
                lines = git_lines(t["repo"], s.split("#", 1)[0])
                sp = d["spans"].get(s)
                if lines is None or sp is None:
                    missing += 1
                    src = ""
                else:
                    src = R.window(lines, sp[0], sp[1], terms, R.PER_ITEM_CAP)
                cands.append(cand(s, src))
            fh.write(json.dumps({"id": d["id"], "query": t["query"], "cands": cands}) + "\n")
    print(f"dev {regime}: candidates without source {missing}", file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1] == "cb":
        prep_cb(sys.argv[2], sys.argv[3])
    else:
        prep_dev(sys.argv[2], sys.argv[3])
