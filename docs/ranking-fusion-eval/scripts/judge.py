#!/usr/bin/env python3
"""Independent relevance judge (TypeSafe System One / Jev) over the top
candidates of each channel, to measure how incomplete commit-derived gold
is. Sends only public OSS data: the task text (a commit message) and the
candidate symbol's path, signature and bounded snippet. Never OXIDE's own
source. Results are cached in judgments.jsonl (one line per (task, cand)).

usage: judge.py tasks.jsonl dump.jsonl <n_tasks> judgments.jsonl
"""
import importlib.util, json, os, random, sys, time
spec = importlib.util.spec_from_file_location(
    "ts", "/home/khoi/Work/oxide/docs/evals/phase-4.2-typesafe/raw/typesafe_client.py")
ts = importlib.util.module_from_spec(spec); spec.loader.exec_module(ts)

tasks = {t["id"]: t for t in map(json.loads, open(sys.argv[1]))}
dump = {d["id"]: d for d in map(json.loads, open(sys.argv[2]))}
n_tasks = int(sys.argv[3]); out_path = sys.argv[4]
done = {}
if os.path.exists(out_path):
    for l in open(out_path):
        j = json.loads(l); done[(j["task"], j["cand"])] = j
random.seed(7)
ids = sorted(dump); random.shuffle(ids); ids = ids[:n_tasks]
INSTR = ("A developer is given the task described in `task` (a change to make in "
         "this codebase). Would a competent developer need to read or modify the "
         "code symbol in `candidate` to carry out that task correctly?")
CRIT = {"relevant": "the symbol is what the task changes, or is directly used by / calls / defines the thing being changed",
        "not_relevant": "the symbol only shares words with the task, or is unrelated to what must change"}
calls = 0
with open(out_path, "a") as f:
    for tid in ids:
        rec = dump[tid]; t = tasks[tid]
        cands = []
        for ch in ("fused", "lexical", "semantic"):
            for row in rec[ch][:5]:
                sid = row[0]
                if sid and sid not in cands: cands.append(sid)
        for sid in cands:
            if (tid, sid) in done: continue
            snip = rec["snippets"].get(sid, {})
            state = {"task": t["query"],
                     "candidate": {"path": sid.split("#")[0], "symbol": sid.split("#", 1)[1],
                                   "kind": snip.get("kind"), "signature": snip.get("signature"),
                                   "source": (snip.get("snippet") or "")[:1200]}}
            try:
                r = ts.noul(state, INSTR, CRIT)
            except Exception as e:
                print("ERR", tid, sid, str(e)[:120], file=sys.stderr); time.sleep(2); continue
            j = {"task": tid, "cand": sid, "noul": r["noul"], "gold": sid in t["gold"]}
            f.write(json.dumps(j) + "\n"); f.flush(); done[(tid, sid)] = j; calls += 1
print(f"judged {calls} new pairs; total {len(done)}", file=sys.stderr)
