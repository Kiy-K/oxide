#!/usr/bin/env python3
"""Bounded CPU-configuration sweep for the Laya operational gate (issue #15).

Added after the default fp32 measurement breached gate O, at the user's
request that the software, not the CPU, be ruled out first (protocol §9).
Operational only: it scores the same fixed CB shortlists with the `noul_ab`
question and records warm latency plus numeric parity against the default
fp32 PyTorch configuration. It never reads quality labels.

usage: cpu_sweep.py <config> <ckpt_dir> <shortlists.jsonl> <out.json> [ref.json]
configs:
  torch            stock Agent (fp32, eager); threads from $SWEEP_THREADS
  torch-nocompile  + encoder.config.reference_compile = False (upstream's benchmark setting)
  torch-sorted     + predict_batch(batch_size=4, sort_by_length=True) (upstream length batching)
  torch-bf16       LAYA_CPU_AMP=bf16 autocast (the library's own opt-in)
  torch-int8       torch.ao dynamic int8 quantization of nn.Linear (AVX-VNNI; changes numerics,
                   not an official Laya configuration)
  onnx             upstream scripts/export_onnx.py graph + laya.onnx_agent.ONNXAgent
                   (ONNX Runtime CPU; the agent has no batch API, so one state per call)
Pinning is applied by the caller with `taskset`.
"""
import json
import os
import resource
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("USE_TF", "0")
CFG = sys.argv[1]
if CFG == "torch-bf16":
    os.environ["LAYA_CPU_AMP"] = "bf16"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import laya_score as LS  # noqa: E402

THREADS = int(os.environ.get("SWEEP_THREADS", "6"))
REPS = 2
Q = {"noul_ab": LS.QUESTIONS["noul_ab"]}


def build(ckpt):
    import torch
    torch.set_num_threads(THREADS)
    if CFG == "onnx":
        from laya.onnx_agent import ONNXAgent
        return ONNXAgent(ckpt, onnx_path=os.environ["SWEEP_ONNX"])
    import laya
    agent = laya.load(ckpt, device="cpu")
    agent.model.eval()
    if CFG == "torch-nocompile":
        agent.model.encoder.config.reference_compile = False
    if CFG == "torch-int8":
        # Encoder only: quantizing the decision head's nn.TransformerEncoder
        # Linears breaks torch's fused-path check (`weight` becomes a method);
        # the 2-layer head is a negligible share of the compute.
        agent.model.encoder = torch.ao.quantization.quantize_dynamic(
            agent.model.encoder, {torch.nn.Linear}, dtype=torch.qint8)
    return agent


def score(agent, states):
    if CFG == "onnx":
        res = [agent.system_one(s, Q) for s in states]
    elif CFG == "torch-sorted":
        res = agent.predict_batch(states, Q, batch_size=4, sort_by_length=True)
    else:
        res = agent.predict_batch(states, Q, batch_size=len(states))
    return [r["answers"]["noul_ab"]["noul"] for r in res]


def main(ckpt, shortlists, out, ref_path=None):
    t0 = time.perf_counter()
    agent = build(ckpt)
    load_s = time.perf_counter() - t0
    room = LS.room_for(agent, {**LS.QUESTIONS, **LS.SWAP})
    tasks = [json.loads(l) for l in open(shortlists)]
    lat, probs = [], {}
    for t in tasks:
        states = [LS.build_state(agent.tok, room, c, t["query"])[0] for c in t["cands"]]
        score(agent, states)  # warm-up for this shortlist's shapes
        for _ in range(REPS):
            s = time.perf_counter()
            p = score(agent, states)
            lat.append(time.perf_counter() - s)
        probs[t["id"]] = p
    lat.sort()
    rep = {"config": CFG, "ckpt": ckpt, "threads": THREADS, "affinity": sorted(os.sched_getaffinity(0)),
           "load_s": round(load_s, 2), "room_tokens": room, "sizes": [len(t["cands"]) for t in tasks],
           "warm_shortlist_ms_p50": round(lat[len(lat) // 2] * 1000, 1),
           "warm_shortlist_ms_mean": round(sum(lat) / len(lat) * 1000, 1),
           "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
           "probs": probs}
    if ref_path:
        ref = json.load(open(ref_path))["probs"]
        d = [abs(a - b) for k in probs for a, b in zip(probs[k], ref[k])]
        rank = lambda v: sorted(range(len(v)), key=lambda i: (-v[i], i))  # noqa: E731
        rep["parity_vs_ref"] = {
            "max_abs_dp": round(max(d), 4),
            "decision_flips": sum((a >= 0.5) != (b >= 0.5) for k in probs for a, b in zip(probs[k], ref[k])),
            "shortlists_with_changed_order": sum(rank(probs[k]) != rank(ref[k]) for k in probs)}
    json.dump(rep, open(out, "w"), indent=1)
    print(json.dumps({k: v for k, v in rep.items() if k != "probs"}))


if __name__ == "__main__":
    main(*sys.argv[2:6])
