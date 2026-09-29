#!/usr/bin/env python3
"""Axis 2: code-review false positives, scoped (per user decision) to
`oxide review`'s context relevance -- is the evidence it surfaces for a git
diff actually relevant to reviewing that diff? Compares OXIDE's own
review-context selection against TypeSafe's independent Noul judgment, both
scored against docs/evals/phase-4.2-typesafe/raw/review_relevance_labels.json
(manually labeled from the real fixture code and the real `oxide review`
output in review_fixture_output.json).

OXIDE's own "relevant" decision is read off `related[].score`: every score in
review_fixture_output.json falls cleanly into ~2.0/~1.0 (has a structural
reason: child/sibling/test/uses/imported-definition) or ~0.01-0.02
(semantic-neighbor only, near-zero semantic score) -- a real bimodal split in
the data, not an arbitrary cutoff. >=1.0 is treated as OXIDE predicting
"relevant".

Sends only the two small eval-agent fixtures (already non-proprietary test
fixtures used throughout this repo's own evals) to TypeSafe -- never OXIDE's
own `src/`.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import typesafe_client as ts  # noqa: E402

RAW = Path(__file__).resolve().parent
REVIEW_OUT = json.loads((RAW / "review_fixture_output.json").read_text())
LABELS = json.loads((RAW / "review_relevance_labels.json").read_text())
OUT = RAW / "axis2_results.json"

OXIDE_RELEVANT_THRESHOLD = 1.0

INSTRUCTIONS = (
    "Given the code change described in `diff` and the candidate code symbol "
    "in `candidate`, would a competent reviewer need to look at this "
    "candidate symbol to verify the change is correct and complete?"
)
CRITERIA = {
    "true": "the candidate is a caller, callee, sibling with shared state, "
            "defining type, or a test that exercises the changed behavior",
    "false": "the candidate has no functional relationship to what the diff "
             "actually changed, even if it lives nearby or in the same file",
}

DIFF_KEY_TO_REVIEW = {
    "py_retry_bug": ("py_retry", "bug_review"),
    "py_retry_clean": ("py_retry", "clean_review"),
    "ts_store_bug": ("ts_store", "bug_review"),
    "ts_store_clean": ("ts_store", "clean_review"),
}


def find_symbol_snippet(review_json, qualified_name):
    for bucket in ("changed_symbols", "related"):
        for s in review_json.get(bucket, []):
            if s["qualified_name"] == qualified_name:
                return {
                    "qualified_name": s["qualified_name"],
                    "kind": s["kind"],
                    "signature": s.get("signature"),
                    "file": s["file"],
                }
    return None


def score(rows, pred_key):
    tp = sum(1 for r in rows if r["ground_truth"] and r[pred_key])
    fp = sum(1 for r in rows if not r["ground_truth"] and r[pred_key])
    tn = sum(1 for r in rows if not r["ground_truth"] and not r[pred_key])
    fn = sum(1 for r in rows if r["ground_truth"] and not r[pred_key])
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    fpr = fp / (fp + tn) if (fp + tn) else float("nan")
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": precision,
            "recall": recall, "false_positive_rate": fpr}


def main():
    all_rows = []
    for diff_key, (repo_key, review_key) in DIFF_KEY_TO_REVIEW.items():
        review_json = REVIEW_OUT[repo_key][review_key]
        diff_desc = LABELS[diff_key]["diff"]
        changed = [s["signature"] or s["qualified_name"] for s in review_json.get("changed_symbols", [])]
        diff_state = {
            "diff_description": diff_desc,
            "changed_symbols": changed,
        }

        related_by_name = {s["qualified_name"]: s for s in review_json.get("related", [])}

        for label in LABELS[diff_key]["labels"]:
            sym_name = label["symbol"]
            rel_entry = related_by_name.get(sym_name)
            oxide_score = rel_entry["score"] if rel_entry else None
            oxide_relevant = (oxide_score is not None and oxide_score >= OXIDE_RELEVANT_THRESHOLD)

            candidate = find_symbol_snippet(review_json, sym_name) or {"qualified_name": sym_name}
            state = {"diff": diff_state, "candidate": candidate}
            r = ts.noul(state, INSTRUCTIONS, criteria=CRITERIA)
            ts_relevant = r["noul"] >= 0.5

            row = {
                "diff_key": diff_key, "symbol": sym_name,
                "ground_truth": label["relevant"], "reason": label["reason"],
                "oxide_score": oxide_score, "oxide_relevant": oxide_relevant,
                "typesafe_noul": r["noul"], "typesafe_relevant": ts_relevant,
                "latency_s": r["latency_s"], "input_tokens": r["input_tokens"],
            }
            all_rows.append(row)
            print(f"  {diff_key:16s} {sym_name:45s} gt={label['relevant']!s:5s} "
                  f"oxide={oxide_relevant!s:5s}({oxide_score}) "
                  f"typesafe={ts_relevant!s:5s}({r['noul']:.2f})")

    oxide_scores = score(all_rows, "oxide_relevant")
    ts_scores = score(all_rows, "typesafe_relevant")
    total_tokens = sum(r["input_tokens"] for r in all_rows)
    cost_usd = total_tokens / 1_000_000 * 0.042
    mean_latency = sum(r["latency_s"] for r in all_rows) / len(all_rows)

    print(f"\n=== oxide review (score>={OXIDE_RELEVANT_THRESHOLD} = relevant) ===")
    print(oxide_scores)
    print(f"\n=== TypeSafe Noul (>=0.5 = relevant) ===")
    print(ts_scores)
    print(f"mean_latency={mean_latency:.2f}s total_input_tokens={total_tokens} est_cost_usd={cost_usd:.5f}")

    OUT.write_text(json.dumps({
        "oxide_review": oxide_scores, "typesafe": ts_scores,
        "mean_latency_s": mean_latency, "total_input_tokens": total_tokens,
        "est_cost_usd": cost_usd, "per_pair": all_rows,
    }, indent=2))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
