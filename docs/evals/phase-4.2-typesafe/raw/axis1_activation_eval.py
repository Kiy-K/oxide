#!/usr/bin/env python3
"""Axis 1: activation false positives. Same 34 held-out prompts phase-4.1
used for the regex-heuristic hook (docs/evals/phase-4.1/raw/labeled_prompts.jsonl),
now judged by TypeSafe's Noul primitive instead of a hand-written regex list.
Compares precision/recall/false-positive-rate/latency/cost against phase-4.1's
already-measured regex numbers (precision 1.00, recall 0.80, FPR 0.00).

Sends only the prompt text (a short, already-synthetic eval sentence written
for phase-4.1, never OXIDE's own source) to TypeSafe.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import typesafe_client as ts  # noqa: E402

PROMPTS = Path(__file__).resolve().parents[2] / "phase-4.1/raw/labeled_prompts.jsonl"
OUT = Path(__file__).resolve().parent / "axis1_results.json"

INSTRUCTIONS = (
    "Does this coding task require exploratory discovery of unfamiliar code "
    "(finding where something is implemented from a behavior description) "
    "as opposed to a task that already names the exact file or symbol to "
    "change?"
)
CRITERIA = {
    "true": "the task describes a symptom/behavior and asks to find where it "
            "is handled, with no specific file named",
    "false": "the task already names an exact file, function, or line to "
             "change (a tiny, targeted edit)",
}


def main():
    rows = [json.loads(l) for l in PROMPTS.read_text().splitlines() if l.strip()]
    results = []
    for row in rows:
        r = ts.noul(row["prompt"], INSTRUCTIONS, criteria=CRITERIA)
        fired = r["noul"] >= 0.5
        results.append({
            **row, "typesafe_noul": r["noul"], "typesafe_fired": fired,
            "correct": fired == row["expect_fire"],
            "latency_s": r["latency_s"], "input_tokens": r["input_tokens"],
        })
        print(f"  {row['id']:5s} bucket={row['bucket']} expect={row['expect_fire']!s:5s} "
              f"noul={r['noul']:.2f} fired={fired!s:5s} {'OK' if fired == row['expect_fire'] else 'MISS'}")

    tp = sum(1 for r in results if r["expect_fire"] and r["typesafe_fired"])
    fp = sum(1 for r in results if not r["expect_fire"] and r["typesafe_fired"])
    tn = sum(1 for r in results if not r["expect_fire"] and not r["typesafe_fired"])
    fn = sum(1 for r in results if r["expect_fire"] and not r["typesafe_fired"])
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    fpr = fp / (fp + tn) if (fp + tn) else float("nan")
    total_tokens = sum(r["input_tokens"] for r in results)
    cost_usd = total_tokens / 1_000_000 * 0.042
    mean_latency = sum(r["latency_s"] for r in results) / len(results)

    print(f"\nprecision={precision:.2f} recall={recall:.2f} fpr={fpr:.2f}")
    print(f"tp={tp} fp={fp} tn={tn} fn={fn}")
    print(f"mean_latency={mean_latency:.2f}s total_input_tokens={total_tokens} est_cost_usd={cost_usd:.5f}")
    print("\nvs. phase-4.1 regex heuristic: precision=1.00 recall=0.80 fpr=0.00 mean_latency=0.04s cost=$0 (no API)")

    OUT.write_text(json.dumps({
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": precision, "recall": recall, "false_positive_rate": fpr,
        "mean_latency_s": mean_latency, "total_input_tokens": total_tokens,
        "est_cost_usd": cost_usd, "per_prompt": results,
    }, indent=2))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
