"""Tables and the preregistered verdict for Decision Study v3 (preregistration.md).

    python docs/phase-6/decision-study-v3/scripts/report.py SCORED --sent REQUESTS \
        [--ledger LEDGER.json]

SCORED is ``score.py`` run with ``compact=`` and ``compact-repeat=`` (``call.py``
judgments); REQUESTS is the ``--requests-only`` run whose request files were sent. The
validity checks are asserted before any number is printed. Quality is reported end to end
(every task: a failed request is a fallback and packs production) and conditional on an
answered request; the gates are end to end.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

V3 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V3.parent / "decision-study-v1" / "scripts"))
sys.path.insert(0, str(V3.parent / "decision-study-v2" / "scripts"))

import verdict as v2  # noqa: E402  (v2's tables, admitted-set helpers, bootstrap)
from gate import spearman  # noqa: E402

PREREGISTRATION = V3 / "preregistration.md"
BUDGETS = v2.BUDGETS
PRIMARY, REPEAT = "compact~order", "compact-repeat~order"
ARMS = {PRIMARY: "compact", REPEAT: "compact-repeat"}
CAP_USD = 0.20
MALFORMED = ("wrong model", "incomplete", "unparsed")
Tasks = list[dict[str, Any]]


def lr(t: dict[str, Any], arm: str, b: str) -> float:
    return float(t["arms"][arm][b]["line_recall"])


def mean_delta(tasks: Tasks, arm: str, b: str, base: str = "heuristic") -> float:
    return statistics.fmean(lr(t, arm, b) - lr(t, base, b) for t in tasks) if tasks else 0.0


def wilson(k: int, n: int) -> tuple[float, float]:
    z, p = 1.96, k / n
    mid, half = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (mid - half) / (1 + z * z / n)), (mid + half) / (1 + z * z / n)


def cluster_bootstrap(deltas: dict[str, list[float]], n: int = 10_000) -> tuple[float, float]:
    """Repositories resampled with replacement, each with all its tasks; seed 0."""
    rng, repos = random.Random(0), sorted(deltas)
    means = sorted(
        statistics.fmean([d for r in rng.choices(repos, k=len(repos)) for d in deltas[r]])
        for _ in range(n)
    )
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def answered(results: dict[str, Any], name: str, task: str) -> bool:
    return bool(results["recorded"][name]["requests"].get(task, {}).get("status") == 200)


def validity(scored: Path, sent: Path, results: dict[str, Any], items: dict[str, Any]) -> None:
    """Asserted: what was sent is what is scored; every unanswered request packs exactly
    production. (An answered request also falls back if an admitted candidate had no
    sendable capsule: v2's rule; counted under OPERATIONS.)"""
    digests = json.loads((sent / "requests.sha256.json").read_text())
    assert json.loads((scored / "requests.sha256.json").read_text()) == digests, "requests differ"
    for name in ARMS.values():
        assert results["recorded"][name]["requests_sha256"] == digests, f"{name} sent other bytes"
        assert set(results["recorded"][name]["requests"]) == {t["task"] for t in results["tasks"]}
    assert json.loads((scored / "leakcheck.json").read_text())["ok"]
    for t in results["tasks"]:
        packed = items[t["task"]]
        for arm, name in ARMS.items():
            if answered(results, name, t["task"]) and not v2.fell_back(t, arm):
                continue
            assert all(packed[arm][b] == packed["heuristic"][b] for b in BUDGETS), t["task"]
            assert all(lr(t, arm, b) == lr(t, "heuristic", b) for b in BUDGETS), t["task"]


def pct(xs: list[float], q: float) -> float:
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


def operations(results: dict[str, Any], n: int) -> dict[str, Any]:
    out = {}
    for name in ARMS.values():
        reqs = list(results["recorded"][name]["requests"].values())
        statuses = Counter(str(r["status"]) for r in reqs)
        sent = sorted(r["latency_s"] for r in reqs if r["status"] != "not sent: cost cap")
        ok = sorted(r["latency_s"] for r in reqs if r["status"] == 200)
        failed = n - statuses["200"]
        lo, hi = wilson(failed, n)
        tokens = [r["input_tokens"] for r in reqs if r.get("input_tokens")] or [0]
        out[name] = {"failed": failed, "statuses": statuses, "tokens": tokens}
        out[name]["usd"] = sum(r.get("usd", 0.0) for r in reqs)
        print(
            f"{name}: {n} requests, {failed} without an answer by {v2.DEADLINE} s "
            f"(rate {failed / n:.3f}, Wilson 95% {lo:.3f} to {hi:.3f}); statuses {dict(statuses)}; "
            f"latency, sent: p50 {pct(sent, 0.5):.2f} p95 {pct(sent, 0.95):.2f} max "
            f"{pct(sent, 1.0):.2f} s; answered: p50 {pct(ok, 0.5):.2f} p95 {pct(ok, 0.95):.2f} s; "
            f"billed tokens/request {statistics.fmean(tokens):.0f}; ${out[name]['usd']:.4f}"
        )
    return out


def regressions(tasks: Tasks, arm: str) -> list[tuple[str, str, float]]:
    return [
        (t["task"], b, round(d, 3))
        for t in tasks
        for b in BUDGETS
        if (d := lr(t, arm, b) - lr(t, "heuristic", b)) < -0.005
    ]


def main(argv: list[str]) -> None:
    scored, sent = Path(argv[0]), Path(argv[argv.index("--sent") + 1])
    ledger = Path(argv[argv.index("--ledger") + 1]) if "--ledger" in argv else None
    results = json.loads((scored / "results.json").read_text())
    items = json.loads((scored / "items.json").read_text())
    tasks: Tasks = results["tasks"]
    n = len(tasks)
    corpus = {t["id"]: t["corpus"] for t in json.loads((V3 / "tasks.json").read_text())["tasks"]}
    corpus = defaultdict(lambda: "baseline-dev@", corpus)  # the dev validity run
    validity(scored, sent, results, items)
    digest = "sha256:" + hashlib.sha256(PREREGISTRATION.read_bytes()).hexdigest()
    print(f"preregistration {digest}")
    print(f"split {results['split']}: {n} tasks with verified gold; validity checks pass\n")

    print("END TO END (every task; a failed request packs production)")
    r = v2.table(tasks, list(tasks[0]["arms"]))
    deltas = v2.per_task(tasks, PRIMARY)
    lo, hi = v2.bootstrap(deltas)
    by_repo: dict[str, list[float]] = defaultdict(list)
    for t, d in zip(tasks, deltas, strict=True):
        by_repo[corpus[t["task"]].split("@")[0]].append(d)
    clo, chi = cluster_bootstrap(by_repo)
    base, p = r["heuristic"], r[PRIMARY]
    gain = p["4096"] - base["4096"]
    rnd = r["random~order (mean of 5)"]["4096"]
    wins, losses = sum(d > 0.005 for d in deltas), sum(d < -0.005 for d in deltas)
    print(
        f"\n{PRIMARY}: Δ@256/1024/4096 "
        + " ".join(f"{p[b] - base[b]:+.4f}" for b in BUDGETS)
        + f"; Δ@4096 task bootstrap 95% CI {lo:+.4f} … {hi:+.4f}, repository-cluster "
        f"{clo:+.4f} … {chi:+.4f}; tasks +{wins} -{losses} ={n - wins - losses}"
    )
    for name, ds in sorted(by_repo.items()):
        rest = [d for k, v in by_repo.items() if k != name for d in v]
        print(
            f"  {name:34} n={len(ds):2}  Δ@4096 {statistics.fmean(ds):+.4f}  "
            f"without it {statistics.fmean(rest) if rest else math.nan:+.4f}"
        )
    for arm in (PRIMARY, REPEAT):
        print(
            f"  regressions, {arm} (task, budget, Δ < -0.005): {regressions(tasks, arm) or 'none'}"
        )

    print("\nCONDITIONAL ON AN ANSWERED REQUEST (reported, not gated)")
    for arm, name in ARMS.items():
        judged = [t for t in tasks if answered(results, name, t["task"])]
        cd = [lr(t, arm, "4096") - lr(t, "heuristic", "4096") for t in judged]
        ci = v2.bootstrap(cd) if len(cd) > 1 else (math.nan, math.nan)
        h = statistics.fmean(lr(t, "heuristic", "4096") for t in judged) if judged else math.nan
        print(
            f"{arm}: answered {len(judged)}/{n}; Δ@256/1024/4096 "
            + " ".join(f"{mean_delta(judged, arm, b):+.4f}" for b in BUDGETS)
            + f" (Δ@4096 CI {ci[0]:+.4f} … {ci[1]:+.4f}); heuristic @4096 on them {h:.4f}; "
            f"the {n - len(judged)} unanswered pack production (validity); answered but "
            f"fallen back (an admitted candidate unsendable): "
            f"{sum(v2.fell_back(t, arm) for t in judged)}"
        )
    both = [t for t in tasks if all(answered(results, nm, t["task"]) for nm in ARMS.values())]
    rhos = []
    for t in both:
        a, b = v2.admitted(t, PRIMARY), v2.admitted(t, REPEAT)
        if (s := spearman([v for v, _ in a], [v for v, _ in b])) is not None:
            rhos.append(s)
    rho = statistics.median(rhos) if rhos else None
    dd = {b: round(mean_delta(both, REPEAT, b, PRIMARY), 4) for b in BUDGETS}
    print(
        f"repeat vs primary, {len(both)} tasks both answered: "
        f"median Spearman (admitted) {rho}, ΔΔ {dd}"
    )

    print("\nOPERATIONS")
    ops = operations(results, n)
    billed = sum(o["usd"] for o in ops.values())
    total = json.loads(ledger.read_text())["usd"] if ledger else billed
    per_point = statistics.fmean(ops["compact"]["tokens"]) / (100 * gain) if gain > 0 else math.inf
    print(f"spend: this run ${billed:.4f}; ledger ${total:.4f} of ${CAP_USD}; "
          f"billed tokens per request per +1 recall point {per_point:.0f}")  # fmt: skip

    statuses = [s for o in ops.values() for s in o["statuses"].elements()]
    bad = sum(s.startswith(MALFORMED) for s in statuses)
    unsent = statuses.count("not sent: cost cap")
    checks = {
        "Q1 recall": gain >= 0.020 and lo > 0,
        "Q2 no harm": all(p[b] >= base[b] - 0.005 for b in BUDGETS),
        "Q3 random order": p["4096"] - rnd >= 0.020,
        "Q4 regressions": losses <= 0.2 * n and losses < wins,
        "Q5 reproducibility": r[REPEAT]["4096"] - base["4096"] >= 0.020 and (rho or 0.0) >= 0.90,
        "O1 failures": ops["compact"]["failed"] <= 0.10 * n,
        "O2 replies": bad == 0,
        "O3 spend": total <= CAP_USD and unsent == 0,
    }
    for k, ok in checks.items():
        print(f"  {k}: {'pass' if ok else 'FAIL'}")
    judged = [t for t in tasks if answered(results, "compact", t["task"])]
    conditional = mean_delta(judged, PRIMARY, "4096") if len(judged) >= 10 else None
    if all(checks.values()):
        print("VERDICT: PASS")
    elif (
        gain <= 0
        or not checks["Q3 random order"]
        or not checks["Q4 regressions"]
        or (conditional is not None and conditional <= 0)
    ):
        print("VERDICT: REJECT")
    else:
        print("VERDICT: INCONCLUSIVE")


if __name__ == "__main__":
    main(sys.argv[1:])
