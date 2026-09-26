#!/usr/bin/env python3
"""Laya relevance scoring for issue #15 (research only; runs in the isolated
venv at ~/.cache/oxide-laya-eval/venv, never imported by OXIDE).

Input: shortlist JSONL, one line per task:
  {"id", "query", "cands": [{"sid", "path", "symbol", "source"}, ...]}
(`source` is the candidate's render_snippet window, built by evaluate.py.)
Output: JSONL, one line per task: {"id", "sids", "scores": {question: [P(relevant)...]},
"error", "ms", "room", "trunc"}. Nothing about gold ever enters this process.

usage:
  laya_score.py ops   <ckpt_dir> <shortlists.jsonl> <out.json> [threads]
  laya_score.py score <ckpt_dir> <shortlists.jsonl> <out.jsonl> [threads] [q1,q2,...]
`score` uses length-sorted batches of 4 (measured identical to one full
batch within Laya's 1e-4 output rounding, results/ops/sweep); the optional
question list restricts which of noul_ab / choice_ab / choice_ba are asked.
"""
import json
import os
import resource
import sys
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
T_START = time.perf_counter()

# Instruction and criteria reused verbatim from
# docs/ranking-fusion-eval/scripts/judge.py (the Jev judge), so Laya and Jev
# answer the same question.
INSTR = ("A developer is given the task described in `task` (a change to make in "
         "this codebase). Would a competent developer need to read or modify the "
         "code symbol in `candidate` to carry out that task correctly?")
REL = "the symbol is what the task changes, or is directly used by / calls / defines the thing being changed"
NOT = "the symbol only shares words with the task, or is unrelated to what must change"

QUESTIONS = {
    # Q1: noul, neutral model-facing labels (README #156 label-following).
    "noul_ab": {"type": "noul", "instructions": INSTR,
                "criteria": {"true": REL, "false": NOT}, "labels": {"true": "A", "false": "B"}},
    # Q2: two-option choice, A = relevant.
    "choice_ab": {"type": "choice", "instructions": INSTR, "criteria": {"A": REL, "B": NOT}},
}
# Label-swap control for Q2 (not a variant): B = relevant.
SWAP = {"choice_ba": {"type": "choice", "instructions": INSTR, "criteria": {"A": NOT, "B": REL}}}
Q_CAP = 128  # task tokens kept; the rest of the state budget goes to the candidate


def p_relevant(qid, ans):
    if qid == "noul_ab":
        return ans["noul"]
    if qid == "choice_ab":
        return ans["probabilities"]["A"]
    if qid == "choice_ba":
        return ans["probabilities"]["B"]
    raise KeyError(qid)


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def load(ckpt, threads):
    import torch
    torch.set_num_threads(threads)
    import laya
    t0 = time.perf_counter()
    agent = laya.load(ckpt, device="cpu")
    agent.model.eval()
    return agent, time.perf_counter() - t0


def room_for(agent, questions):
    """State tokens that fit beside the longest question's header+options."""
    from laya.common import build_sequence
    max_len = agent.cfg.get("max_len", 512)
    head = agent.cfg.get("head_max_len", 192)
    rooms = []
    for q in questions.values():
        internal = agent._to_internal(q)
        ids, _ = build_sequence(agent.tok, "", internal, max_len, head)
        rooms.append(max_len - len(ids))  # header + options + both SEPs are already in ids
    return min(rooms)


def build_state(tok, room, c, query):
    """Plain-text state, task first then candidate, both bounded so nothing
    is right-truncated by Laya itself. Returns (text, truncation flags)."""
    q_ids = tok(query, add_special_tokens=False)["input_ids"]
    q_cut = len(q_ids) > Q_CAP
    q_text = tok.decode(q_ids[:Q_CAP]) if q_cut else query
    head = f"Task: {q_text}\n\nCandidate: {c['path']} :: {c['symbol']}\n\nSource:\n"
    h_len = len(tok(head, add_special_tokens=False)["input_ids"])
    s_ids = tok(c["source"], add_special_tokens=False)["input_ids"]
    s_room = max(0, room - h_len - 2)
    s_cut = len(s_ids) > s_room
    src = tok.decode(s_ids[:s_room]) if s_cut else c["source"]
    return head + src, {"query_cut": q_cut, "source_cut": s_cut,
                        "source_tokens": len(s_ids), "source_room": s_room}


def score_states(agent, states, questions, sorted_batches=False):
    if sorted_batches:
        res = agent.predict_batch(states, questions, batch_size=4, sort_by_length=True)
    else:
        res = agent.predict_batch(states, questions, batch_size=len(states))
    return {qid: [p_relevant(qid, r["answers"][qid]) for r in res] for qid in questions}


def cmd_ops(ckpt, shortlists, out, threads):
    t_imported = time.perf_counter()
    agent, t_load = load(ckpt, threads)
    report = {"ckpt": ckpt, "threads": threads,
              "cold_process_to_loaded_s": round(time.perf_counter() - T_START, 3),
              "load_s": round(t_load, 3), "rss_after_load_mb": round(rss_mb(), 1)}
    tasks = [json.loads(l) for l in open(shortlists)]
    room = room_for(agent, {**QUESTIONS, **SWAP})
    report["room_tokens"] = room
    one = {"noul_ab": QUESTIONS["noul_ab"]}
    # first call pays one-off allocator costs; reported separately
    states = [build_state(agent.tok, room, c, tasks[0]["query"])[0] for c in tasks[0]["cands"]]
    t0 = time.perf_counter()
    score_states(agent, states, one)
    report["first_call_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    lat, per_pair, det_ok = [], [], True
    trunc = {"query_cut": 0, "source_cut": 0, "n": 0}
    for t in tasks:
        built = [build_state(agent.tok, room, c, t["query"]) for c in t["cands"]]
        states = [b[0] for b in built]
        for _, f in built:
            trunc["n"] += 1
            trunc["query_cut"] += f["query_cut"]
            trunc["source_cut"] += f["source_cut"]
        runs = []
        for _ in range(3):
            t0 = time.perf_counter()
            runs.append(score_states(agent, states, one))
            dt = time.perf_counter() - t0
            lat.append(dt)
            per_pair.append(dt / len(states))
        det_ok &= all(r == runs[0] for r in runs)
    lat.sort()
    per_pair.sort()
    report.update({
        "tasks": len(tasks), "shortlist_sizes": [len(t["cands"]) for t in tasks],
        "warm_shortlist_ms_p50": round(lat[len(lat) // 2] * 1000, 1),
        "warm_shortlist_ms_p90": round(lat[int(0.9 * len(lat))] * 1000, 1),
        "warm_shortlist_ms_max": round(lat[-1] * 1000, 1),
        "warm_per_pair_ms_p50": round(per_pair[len(per_pair) // 2] * 1000, 1),
        "deterministic_repeat": det_ok, "truncation": trunc,
        "peak_rss_mb": round(rss_mb(), 1),
    })
    json.dump(report, open(out, "w"), indent=1)
    print(json.dumps(report, indent=1))


def cmd_score(ckpt, shortlists, out, threads, only=None):
    agent, _ = load(ckpt, threads)
    qs = {**QUESTIONS, **SWAP}
    room = room_for(agent, qs)  # room is fixed by the full question set, so inputs never depend on `only`
    if only:
        qs = {k: qs[k] for k in only.split(",")}
    done = set()
    if os.path.exists(out):
        done = {json.loads(l)["id"] for l in open(out)}
    with open(out, "a") as fh:
        for l in open(shortlists):
            t = json.loads(l)
            if t["id"] in done:
                continue
            built = [build_state(agent.tok, room, c, t["query"]) for c in t["cands"]]
            t0 = time.perf_counter()
            try:
                scores, err = score_states(agent, [b[0] for b in built], qs, sorted_batches=True), None
            except Exception as e:  # counted; the task falls back to production order
                scores, err = None, repr(e)[:300]
            fh.write(json.dumps({
                "id": t["id"], "sids": [c["sid"] for c in t["cands"]], "scores": scores,
                "error": err, "ms": round((time.perf_counter() - t0) * 1000, 1),
                "room": room, "trunc": [b[1] for b in built]}) + "\n")
            fh.flush()
    print(f"peak_rss_mb {rss_mb():.0f}", file=sys.stderr)


if __name__ == "__main__":
    cmd, ckpt, inp, out = sys.argv[1:5]
    threads = int(sys.argv[5]) if len(sys.argv) > 5 else 6
    if cmd == "score":
        cmd_score(ckpt, inp, out, threads, sys.argv[6] if len(sys.argv) > 6 else None)
    else:
        cmd_ops(ckpt, inp, out, threads)
