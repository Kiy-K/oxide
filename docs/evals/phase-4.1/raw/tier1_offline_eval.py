#!/usr/bin/env python3
"""Phase 4.1 Tier 1: offline precision/recall of the UserPromptSubmit hook's
trigger heuristic (contrib/agent-hooks/claude-code/oxide_suggest.py) over a
held-out labeled prompt set. Zero agent runs -- the hook's trigger is a pure
function of prompt text plus index-file presence, so it is measured directly
by invoking the hook script as Claude Code would (JSON on stdin, JSON or
silence on stdout), the same way phase-3.1's advisor-recommended split
separates deterministic measurement from agent-run measurement.
"""
import json
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HOOK = ROOT / "contrib/agent-hooks/claude-code/oxide_suggest.py"
PROMPTS = Path(__file__).resolve().parent / "labeled_prompts.jsonl"


def run_hook(prompt: str, cwd: str, session_id: str = "eval-session"):
    payload = json.dumps(
        {
            "session_id": session_id,
            "transcript_path": "/dev/null",
            "cwd": cwd,
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
            "prompt": prompt,
        }
    )
    start = time.perf_counter()
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=5,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    fired = bool(r.stdout.strip())
    if fired:
        out = json.loads(r.stdout)
        assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "additionalContext" in out["hookSpecificOutput"]
    return fired, elapsed_ms, r.returncode


def main():
    rows = [json.loads(l) for l in PROMPTS.read_text().splitlines() if l.strip()]
    # Unique per invocation of this script, not per prompt id: a fixed id like
    # "eval-A1" would collide with any state left behind by a previous run of
    # this same script (the hook's one-suggestion-per-session cap is a real,
    # intentional feature -- see oxide_suggest.py -- so this harness must not
    # let its own re-runs, or manual testing under the same ids, trip it).
    run_id = uuid.uuid4().hex[:8]

    with tempfile.TemporaryDirectory() as tmp:
        indexed = Path(tmp) / "indexed_repo"
        (indexed / ".oxide").mkdir(parents=True)
        (indexed / ".oxide" / "index.db").write_text("fake")
        unindexed = Path(tmp) / "unindexed_repo"
        unindexed.mkdir()

        results = []
        latencies = []
        for row in rows:
            # Every session-id must be unique per prompt here, or the
            # per-session suppression flag (a real feature, not a bug) would
            # silence every prompt after the first.
            fired, ms, code = run_hook(
                row["prompt"], str(indexed), session_id=f"eval-{run_id}-{row['id']}"
            )
            latencies.append(ms)
            correct = fired == row["expect_fire"]
            results.append({**row, "fired": fired, "correct": correct, "ms": ms, "exit_code": code})
            assert code == 0, f"hook must always exit 0, got {code} for {row['id']}"

        # Unindexed-repo control: every prompt, including strong Bucket-A
        # ones, must produce silence when there is no local index to query.
        unindexed_fires = 0
        for row in rows:
            fired, _, code = run_hook(
                row["prompt"], str(unindexed), session_id=f"unindexed-{run_id}-{row['id']}"
            )
            assert code == 0
            if fired:
                unindexed_fires += 1

    by_bucket = {}
    for r in results:
        by_bucket.setdefault(r["bucket"], []).append(r)

    print("=== Tier 1: offline heuristic accuracy (indexed repo) ===")
    tp = fp = tn = fn = 0
    for bucket in sorted(by_bucket):
        grp = by_bucket[bucket]
        should_fire = grp[0]["expect_fire"]
        fired_n = sum(1 for r in grp if r["fired"])
        correct_n = sum(1 for r in grp if r["correct"])
        print(
            f"  bucket {bucket} (expect_fire={should_fire}): "
            f"{fired_n}/{len(grp)} fired, {correct_n}/{len(grp)} correct"
        )
        for r in grp:
            if r["expect_fire"] and r["fired"]:
                tp += 1
            elif r["expect_fire"] and not r["fired"]:
                fn += 1
            elif not r["expect_fire"] and r["fired"]:
                fp += 1
            else:
                tn += 1
        if not all(r["correct"] for r in grp):
            print("    misses:", [r["id"] for r in grp if not r["correct"]])

    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    fpr = fp / (fp + tn) if (fp + tn) else float("nan")
    print(f"\n  precision={precision:.2f} recall={recall:.2f} false_positive_rate={fpr:.2f}")
    print(f"  tp={tp} fp={fp} tn={tn} fn={fn}")

    print(f"\n=== Unindexed-repo control ===")
    print(f"  {unindexed_fires}/{len(rows)} prompts fired with no .oxide/index.db present (must be 0)")

    print(f"\n=== Hook latency (subprocess spawn + heuristic, n={len(latencies)}) ===")
    latencies.sort()
    print(f"  mean={sum(latencies)/len(latencies):.1f}ms  p50={latencies[len(latencies)//2]:.1f}ms  "
          f"max={max(latencies):.1f}ms")

    out_path = Path(__file__).resolve().parent / "tier1_results.json"
    out_path.write_text(json.dumps({
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": precision, "recall": recall, "false_positive_rate": fpr,
        "unindexed_control_fires": unindexed_fires,
        "n_prompts": len(rows),
        "latency_ms": {"mean": sum(latencies)/len(latencies), "max": max(latencies)},
        "per_prompt": results,
    }, indent=2))
    print(f"\nWrote {out_path}")

    assert unindexed_fires == 0, "REGRESSION: hook fired without a local index"


if __name__ == "__main__":
    main()
