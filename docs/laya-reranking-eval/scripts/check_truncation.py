#!/usr/bin/env python3
"""Tokenizer-only check (no inference) that `laya_score.build_state` fits every
state inside the checkpoint's window, i.e. Laya itself never right-truncates.
usage: check_truncation.py <ckpt_dir> <shortlists.jsonl>  (runs in the Laya venv)"""
import json, os, sys
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1"); os.environ.setdefault("USE_TF", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import laya_score as LS  # noqa: E402
from laya.agent import Agent, _load_tokenizer, _fix_tokenizer_config  # noqa: E402
from laya.common import build_sequence  # noqa: E402

ckpt, inp = sys.argv[1:3]
cfg = json.load(open(os.path.join(ckpt, "rl_agent_config.json")))
_fix_tokenizer_config(ckpt)
tok = _load_tokenizer(os.path.join(ckpt, "tokenizer"), cfg)
max_len, head = cfg["max_len"], cfg["head_max_len"]
qs = {**LS.QUESTIONS, **LS.SWAP}
room = min(max_len - len(build_sequence(tok, "", Agent._to_internal(q), max_len, head)[0]) for q in qs.values())
n = viol = 0
lens = []
for l in open(inp):
    t = json.loads(l)
    for c in t["cands"]:
        text, _ = LS.build_state(tok, room, c, t["query"])
        for q in qs.values():
            full, _ = build_sequence(tok, text, Agent._to_internal(q), 100000, head)
            n += 1
            viol += len(full) > max_len
            lens.append(len(full))
lens.sort()
print(json.dumps({"ckpt": ckpt, "room": room, "sequences": n, "laya_truncated": viol,
                  "seq_tokens_mean": round(sum(lens) / len(lens), 1), "seq_tokens_p50": lens[len(lens) // 2],
                  "seq_tokens_max": lens[-1]}))
