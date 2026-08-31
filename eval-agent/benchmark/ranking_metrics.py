#!/usr/bin/env python3
"""Ranking metrics over the pinned Tier A set: Recall@K, nDCG@10, MRR,
first-useful-hit, tokens. Read-only vs OXIDE."""
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, "/home/khoi/Projects/oxide/scripts/agent_eval")
import contextbench_run as cb  # noqa: E402

ROOT = cb.ROOT
# Fail fast: partial embedder env silently re-labels vectors (http:@…) and
# wipes/rebuilds indexes under an incomparable provider identity. Two valid
# configurations: HTTP (llama.cpp, requires exact model label) or native
# in-process fastembed (OXIDE_EMBED_NATIVE, native-embed build).
ENV = {
    "OXIDE_EMBED_URL": os.environ.get("OXIDE_EMBED_URL", ""),
    "OXIDE_EMBED_MODEL": os.environ.get("OXIDE_EMBED_MODEL", ""),
}
NATIVE_PROFILE = os.environ.get("OXIDE_EMBED_NATIVE", "")
if NATIVE_PROFILE:
    assert not ENV["OXIDE_EMBED_URL"], (
        "OXIDE_EMBED_URL and OXIDE_EMBED_NATIVE both set — ambiguous, unset one"
    )
    # Placeholder only: a leftover OXIDE_EMBED_NATIVE_QUERY_PROMPT is silently
    # ignored by Rust for any non-Gemma profile (NativeEmbedder::new only
    # honors it when is_gemma), so guessing the suffix here would mislabel
    # the run. Overwritten below with the ground-truth value read from the
    # index meta once the first repo is indexed and verified.
    MODEL_LABEL = NATIVE_PROFILE
else:
    assert ENV["OXIDE_EMBED_URL"], "set OXIDE_EMBED_URL or OXIDE_EMBED_NATIVE"
    assert ENV["OXIDE_EMBED_MODEL"] == "qwen3-Q8_0", (
        "set OXIDE_EMBED_MODEL=qwen3-Q8_0 — a different label wipes embeddings"
    )
    import json as _json
    import urllib.request
    _req = urllib.request.Request(
        ENV["OXIDE_EMBED_URL"].replace("/embeddings", "/embeddings"),
        data=_json.dumps({"input": "liveness"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(_req, timeout=10)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"embedder not answering at {ENV['OXIDE_EMBED_URL']}: {e}")
    MODEL_LABEL = ENV["OXIDE_EMBED_MODEL"]
PIN = ROOT / "eval-agent/results/tier_a_instances.txt"
ALLOW = {i.strip() for i in PIN.read_text().splitlines() if i.strip()}
CONDITIONS = ["lexical", "vec", "hybrid", "budgeted"]
KS = [1, 3, 5, 10]


def ranked_files(items):
    """Unique files preserving first-occurrence order."""
    seen, out = set(), []
    for it in items:
        f = it["file"]
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def resolve_model_label(native_profile: str, placeholder_label: str, verified_embedder: str) -> str:
    """The label to report once the index's actual embedder is known.

    For a native profile, the ground truth read from the index meta always
    wins over `placeholder_label` (an env-derived guess) — a leftover
    OXIDE_EMBED_NATIVE_QUERY_PROMPT is silently ignored by Rust's
    NativeEmbedder::new for any non-Gemma profile, so reconstructing the
    label from env vars can disagree with what was actually embedded.
    HTTP-mode labeling is unaffected and keeps its placeholder.
    """
    return verified_embedder if native_profile else placeholder_label


def main():
    global MODEL_LABEL
    tasks = [t for t in cb.load_tasks() if t["instance_id"] in ALLOW]
    agg = {c: defaultdict(float) for c in CONDITIONS}
    n = 0
    embedder_verified = False
    for row in tasks:
        repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
        # Unlike contextbench_run.py's main loop, this script never used to
        # call index_repo() itself — it silently assumed indices were
        # already fresh, which breaks the moment two pinned tasks in the
        # same repo pin different base commits (checkout changes the file
        # content the index was built from). Validate/reembed here too.
        cb.index_repo(repo, ENV["OXIDE_EMBED_URL"])
        if not embedder_verified:
            # Catches a binary built without --features native-embed:
            # open_embedder() silently falls through to the offline hashed
            # embedder rather than erroring, which would otherwise let this
            # whole run score the hashed baseline under the native model's
            # label with no indication anything was wrong.
            verified = cb.verify_embedder_took_effect(repo, want_native=bool(NATIVE_PROFILE))
            MODEL_LABEL = resolve_model_label(NATIVE_PROFILE, MODEL_LABEL, verified)
            embedder_verified = True
        problem = row["problem_statement"]
        gold = set(cb.Gold({
            "init_ctx": json.loads(row["gold_context"]),
            "repo_url": row["repo_url"],
            "commit": row["base_commit"],
        }).files())
        n += 1
        for cond in CONDITIONS:
            try:
                items, tok = cb.retrieve(repo, cond, problem)
            except Exception as e:
                print(f"FAIL repo={row['repo']} task={row['instance_id']} cond={cond}: {e}", file=sys.stderr)
                raise
            files = ranked_files(items)
            a = agg[cond]
            a["tok"] += tok
            a["items"] += len(items)
            for k in KS:
                a[f"r@{k}"] += len(gold & set(files[:k])) / max(1, len(gold))
                a[f"hit@{k}"] += float(any(f in gold for f in files[:k]))
            rr = 0.0
            for i, f in enumerate(files):
                if f in gold:
                    rr = 1.0 / (i + 1)
                    break
            a["mrr"] += rr
            dcg = sum(
                1.0 / math.log2(i + 2)
                for i, f in enumerate(files[:10]) if f in gold
            )
            ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(gold), 10)))
            a["ndcg10"] += dcg / ideal if ideal else 0.0
    print(f"tasks={n} model={MODEL_LABEL}")
    hdr = ("cond      " + "".join(f"{'R@'+str(k):>7}" for k in KS)
           + f" {'hit@5':>7} {'MRR':>7} {'nDCG@10':>8} {'tok':>6} {'items':>6}")
    print(hdr)
    for c in CONDITIONS:
        a = agg[c]
        cells = "".join(f"{a[f'r@{k}']/n:>7.3f}" for k in KS)
        print(f"{c:<10}{cells}{a['hit@5']/n:>7.2f}{a['mrr']/n:>7.3f}"
              f"{a['ndcg10']/n:>8.3f}{a['tok']/n:>6.0f}{a['items']/n:>6.1f}")


if __name__ == "__main__":
    main()
