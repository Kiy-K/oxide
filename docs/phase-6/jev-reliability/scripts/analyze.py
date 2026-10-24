"""Policies, distributions and preregistered gates for the JEV reliability study.

    python docs/phase-6/jev-reliability/scripts/analyze.py OUT/attempts.jsonl
    python docs/phase-6/jev-reliability/scripts/analyze.py selfcheck

No network. Reads ``probe.py``'s attempts and evaluates, on the same logical requests:

  fallback   the provisional policy: one attempt; any failure, or no answer by
             ``DEADLINE``, falls back to the deterministic heuristic at once
  retry      one immediate retry of a retryable failure, only if at least ``NEED`` of
             the deadline remains; an answer counts only if it lands by ``DEADLINE``
  breaker    fallback plus a circuit breaker (``K`` consecutive failures open it for
             ``OPEN_S``; the next request is a half-open probe)

Thresholds are those of preregistration.md § Acceptance criteria; nothing is tuned here.
"""

from __future__ import annotations

import itertools
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEADLINE = 2.0  # src/oxide/core/capsule.py DEADLINE, unchanged
NEED = 1.0  # retry only if this much of the deadline remains (~ v2 median success latency)
K, OPEN_S = 3, 15.0  # breaker
BLOCK, REPS, PERMS = 10, 5000, 10000
MIN_FAILURES, MIN_LOGICAL = 20, 300
USD = 0.042 / 1e6  # adapter.MANIFEST usd_per_million_input_tokens

Outcome = tuple[bool, float, int]  # answered by the deadline, time to decision, calls made


def retryable(status: Any) -> bool:  # same as probe.retryable
    if isinstance(status, int):
        return status == 429 or status >= 500
    return status in ("killed", "error")


def logical(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """First and (if any) second attempt per logical request, in start order; unsent count."""
    by: dict[tuple[int, int], dict[int, Any]] = defaultdict(dict)
    for r in rows:
        by[(r["window"], r["logical"])][r["attempt"]] = r
    out, unsent = [], 0
    for key, a in by.items():
        if "start_s" not in a[1]:
            unsent += 1
            continue
        out.append({"key": key, "concurrency": a[1]["concurrency"], "a1": a[1], "a2": a.get(2)})
    out.sort(key=lambda x: (x["key"][0], x["a1"]["start_s"]))
    return out, unsent


def ok(a: dict[str, Any] | None) -> bool:
    return a is not None and a["status"] == 200


def fallback(x: dict[str, Any]) -> Outcome:
    a1 = x["a1"]
    return ok(a1) and a1["latency_s"] <= DEADLINE, min(a1["latency_s"], DEADLINE), 1


def retry(x: dict[str, Any]) -> Outcome:
    a1, a2 = x["a1"], x["a2"]
    first = fallback(x)
    if first[0] or not retryable(a1["status"]) or a1["latency_s"] > DEADLINE - NEED:
        return first
    if a2 is None or "start_s" not in a2:  # not sent (cap): falls back
        return first
    total = a2["start_s"] - a1["start_s"] + a2["latency_s"]
    return ok(a2) and total <= DEADLINE, min(total, DEADLINE), 2


def breaker(xs: list[dict[str, Any]]) -> list[Outcome]:
    """Fallback with a breaker, per window, in start order (state updated at each outcome)."""
    out: list[Outcome] = []
    window, run, open_until = None, 0, -math.inf
    for x in xs:
        if x["key"][0] != window:
            window, run, open_until = x["key"][0], 0, -math.inf
        t = x["a1"]["start_s"]
        if t < open_until:
            out.append((False, 0.0, 0))
            continue
        res = fallback(x)
        run = 0 if res[0] else run + 1
        if run >= K:
            open_until, run = t + res[1] + OPEN_S, K - 1  # half-open: one more failure reopens
        out.append(res)
    return out


def q(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, math.ceil(p * len(s)) - 1)] if s else float("nan")


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return float("nan"), float("nan")
    z, p = 1.96, k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, c - h), c + h


def block_ci(diffs: list[float]) -> tuple[float, float]:
    """Circular moving-block bootstrap of the mean (failures may cluster in time)."""
    n, rng, means = len(diffs), random.Random(0), []
    for _ in range(REPS):
        idx: list[int] = []
        while len(idx) < n:
            s = rng.randrange(n)
            idx += [(s + j) % n for j in range(BLOCK)]
        means.append(sum(diffs[i] for i in idx[:n]) / n)
    return q(means, 0.025), q(means, 0.975)


def burst_p(xs: list[dict[str, Any]]) -> tuple[int, float, float]:
    """Adjacent first-attempt failure pairs within windows vs within-window permutations."""
    seqs: dict[int, list[bool]] = defaultdict(list)
    for x in xs:
        seqs[x["key"][0]].append(not ok(x["a1"]))

    def pairs(ss: list[list[bool]]) -> int:
        return sum(a and b for s in ss for a, b in itertools.pairwise(s))

    obs, rng, hits, total = pairs(list(seqs.values())), random.Random(1), 0, 0
    for _ in range(PERMS):
        n = pairs([rng.sample(s, len(s)) for s in seqs.values()])
        hits += n >= obs
        total += n
    return obs, total / PERMS, (hits + 1) / (PERMS + 1)


def billed(xs: list[dict[str, Any]], outcomes: list[Outcome]) -> float:
    """Billed spend of only the attempts a policy would have made."""
    usd = 0.0
    for x, (_, _, calls) in zip(xs, outcomes, strict=True):
        for a in [x["a1"], x["a2"]][:calls]:
            usd += ((a or {}).get("input_tokens") or 0) * USD
    return usd


def summary(name: str, xs: list[dict[str, Any]], res: list[Outcome]) -> dict[str, Any]:
    n, good = len(res), sum(r[0] for r in res)
    times = [r[1] for r in res]
    return {
        "policy": name,
        "available": good / n,
        "fail": 1 - good / n,
        "fail_ci": wilson(n - good, n),
        "ttd": [q(times, p) for p in (0.5, 0.95, 0.99)],
        "deadline_hits": sum(t >= DEADLINE for t in times) / n,
        "calls": sum(r[2] for r in res) / n,
        "usd_per_success": billed(xs, res) / good if good else float("nan"),
    }


def groups(xs: list[dict[str, Any]], field: Callable[[dict[str, Any]], int]) -> dict[int, Any]:
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for x in xs:
        out[field(x)].append(x)
    return dict(sorted(out.items()))


def describe(xs: list[dict[str, Any]], attempts: list[dict[str, Any]]) -> list[str]:
    lines = [
        f"attempts sent {len(attempts)}; status {dict(Counter(a['status'] for a in attempts))}"
    ]
    lines.append("first-attempt failures by concurrency:")
    for c, g in groups(xs, lambda x: x["concurrency"]).items():
        f = sum(not ok(x["a1"]) for x in g)
        lo, hi = wilson(f, len(g))
        lines.append(f"  c={c}: {f}/{len(g)} = {f / len(g):.3f} (95% CI {lo:.3f}..{hi:.3f})")
        wall = [x["a1"]["latency_s"] for x in g if ok(x["a1"])]
        lines.append(f"        200 latency p50 {q(wall, 0.5):.2f} p95 {q(wall, 0.95):.2f}")
    lines.append("first-attempt failures by window:")
    for w, g in groups(xs, lambda x: x["key"][0]).items():
        f = sum(not ok(x["a1"]) for x in g)
        stamp = time.strftime("%H:%M UTC", time.gmtime(min(x["a1"]["wall_utc"] for x in g)))
        lines.append(f"  w{w} {stamp}: {f}/{len(g)} = {f / len(g):.3f}")
    lines.append("attempt latency, client wall s, p50 / p90 / p95 / p99 / max [network p50]:")
    for label in ("200", "529", "other"):
        sub = [
            a
            for a in attempts
            if str(a["status"]) == label or (label == "other" and a["status"] not in (200, 529))
        ]
        if sub:
            wall = [a["latency_s"] for a in sub]
            net = [a["net_s"] for a in sub if a.get("net_s") is not None]
            stats = " / ".join(f"{q(wall, p):.2f}" for p in (0.5, 0.9, 0.95, 0.99, 1.0))
            lines.append(f"  {label:5} n {len(sub):4}: {stats} [{q(net, 0.5):.2f}]")
    after = Counter(a.get("headers", {}).get("retry-after") for a in attempts if a["status"] != 200)
    lines.append(f"retry-after header on failures: {dict(after)}")
    failed = [x for x in xs if retryable(x["a1"]["status"])]
    sent = [x for x in failed if x["a2"] and "start_s" in x["a2"]]
    lines.append(
        f"retryable first failures {len(failed)}; immediate retry answered "
        f"{sum(ok(x['a2']) for x in sent)}/{len(sent)} (at any latency)"
    )
    return lines


def report(rows: list[dict[str, Any]]) -> list[str]:
    xs, unsent = logical(rows)
    attempts = [r for r in rows if "start_s" in r]
    lines = [f"logical requests evaluated {len(xs)}, not sent (cap) {unsent}"]
    lines += describe(xs, attempts)
    pol = {"fallback": [fallback(x) for x in xs], "retry": [retry(x) for x in xs]}
    pol["breaker"] = breaker(xs)
    s = {k: summary(k, xs, v) for k, v in pol.items()}
    lines.append(
        "policy     avail  fail (95% CI)        ttd p50/p95/p99  deadline  calls  $/answer"
    )
    for v in s.values():
        lo, hi = v["fail_ci"]
        t50, t95, t99 = v["ttd"]
        lines.append(
            f"  {v['policy']:8} {v['available']:.3f}  {v['fail']:.3f} ({lo:.3f}..{hi:.3f})  "
            f"{t50:.2f}/{t95:.2f}/{t99:.2f}   {v['deadline_hits']:.3f}    {v['calls']:.3f}  "
            f"{v['usd_per_success']:.6f}"
        )
    diffs = [float(r[0]) - float(f[0]) for r, f in zip(pol["retry"], pol["fallback"], strict=True)]
    d = sum(diffs) / len(diffs)
    lo, hi = block_ci(diffs)
    lines.append(
        f"retry - fallback availability {d:+.4f} (block bootstrap 95% CI {lo:+.4f}..{hi:+.4f})"
    )
    c4 = [retry(x)[2] for x in xs if x["concurrency"] == 4]
    c4_calls = sum(c4) / len(c4) if c4 else 1.0
    lines.append(f"retry calls per logical request at c=4: {c4_calls:.3f}")
    obs, expected, p = burst_p(xs)
    lines.append(f"adjacent first-failure pairs {obs} vs {expected:.2f} by chance; p {p:.4f}")
    skipped = [x for x, r in zip(xs, pol["breaker"], strict=True) if r[2] == 0]
    avoided = sum(not fallback(x)[0] for x in skipped)
    lost = len(skipped) - avoided
    fb_fail = sum(not r[0] for r in pol["fallback"])
    lines.append(
        f"breaker K={K} open {OPEN_S:.0f}s: skips {len(skipped)} calls; {avoided} would have "
        f"failed, {lost} would have answered"
    )

    nf = sum(retryable(x["a1"]["status"]) for x in xs)
    g: dict[str, bool] = {
        "R0 >= 20 retryable first failures": nf >= MIN_FAILURES,
        "R1 dA >= +0.03 and CI low > 0": d >= 0.03 and lo > 0,
        "R2 ttd p95 rises <= 0.5 s, p99 <= 2.0 s": s["retry"]["ttd"][1] - s["fallback"]["ttd"][1]
        <= 0.5
        and s["retry"]["ttd"][2] <= DEADLINE,
        "R3 calls <= 1.25 (c=4 <= 1.35)": s["retry"]["calls"] <= 1.25 and c4_calls <= 1.35,
        "R4 $/answer <= 1.05x fallback": s["retry"]["usd_per_success"]
        <= 1.05 * s["fallback"]["usd_per_success"],
    }
    use_retry = all(g.values())
    chosen = s["retry" if use_retry else "fallback"]
    g["B0 bursty, p < 0.05"] = nf >= MIN_FAILURES and p < 0.05
    g["B1 skips >= 20% of failures, loses <= 0.01"] = (
        fb_fail > 0 and avoided >= 0.2 * fb_fail and lost / len(xs) <= 0.01
    )
    malformed = sum(
        a["status"] in ("wrong model",) or str(a["status"]).startswith("unparsed") for a in attempts
    )
    w_fail = [
        sum(not (retry(x) if use_retry else fallback(x))[0] for x in w) / len(w)
        for w in groups(xs, lambda x: x["key"][0]).values()
    ]
    g["O0 >= 300 logical requests evaluated"] = len(xs) >= MIN_LOGICAL
    g["O1 fail <= 0.10 and CI high <= 0.15"] = (
        chosen["fail"] <= 0.10 and chosen["fail_ci"][1] <= 0.15
    )
    g["O1w every window fail <= 0.25"] = max(w_fail) <= 0.25
    g["O2 deadline hits <= 0.05, ttd p50 <= 1.2 s"] = (
        chosen["deadline_hits"] <= 0.05 and chosen["ttd"][0] <= 1.2
    )
    g["O3 no malformed or wrong-model reply"] = malformed == 0
    lines.append("")
    lines += [f"{k:46} {'PASS' if v else 'FAIL'}" for k, v in g.items()]
    if all(v for k, v in g.items() if k[0] == "O"):
        verdict = "PASS"
    elif min(s["fallback"]["fail"], s["retry"]["fail"]) > 0.10 or malformed:
        verdict = "FAIL"
    else:
        verdict = "INCONCLUSIVE"
    breaker_ok = g["B0 bursty, p < 0.05"] and g["B1 skips >= 20% of failures, loses <= 0.01"]
    lines.append(
        f"bounded retry: {'RECOMMEND' if use_retry else 'NOT RECOMMENDED'}"
        + ("" if nf >= MIN_FAILURES else " (R0: too few failures to evaluate)")
    )
    lines.append(f"circuit breaker: {'RECOMMEND' if breaker_ok else 'NOT RECOMMENDED'}")
    lines.append(f"OPERATIONAL VERDICT: {verdict} (under {chosen['policy']})")
    return lines


def selfcheck() -> None:
    """A synthetic trace with known answers for every policy."""

    def row(i: int, n: int, t: float, status: Any, lat: float) -> dict[str, Any]:
        return {
            "window": 1,
            "concurrency": 1,
            "logical": i,
            "task": "t",
            "attempt": n,
            "start_s": t,
            "wall_utc": 1e9 + t,
            "status": status,
            "latency_s": lat,
            "net_s": lat - 0.04,
            "headers": {},
            "input_tokens": 1000 if status == 200 else None,
        }

    rows = [
        row(0, 1, 0.0, 200, 0.9),  # answered
        row(1, 1, 2.0, 529, 0.8),  # retried in time: only retry answers
        row(1, 2, 2.81, 200, 1.0),
        row(2, 1, 5.0, 529, 1.2),  # too late to retry (needs 1.0 s left)
        row(2, 2, 6.2, 200, 0.5),
        row(3, 1, 7.0, 200, 2.5),  # answered after the deadline: falls back at 2.0 s
        row(4, 1, 10.0, 400, 0.3),  # not retryable
        row(5, 1, 11.0, 529, 0.8),
        {"window": 1, "logical": 6, "attempt": 1, "concurrency": 1, "status": "not sent: cost cap"},
    ]
    xs, unsent = logical(rows)
    assert unsent == 1 and len(xs) == 6
    assert [fallback(x)[0] for x in xs] == [True, False, False, False, False, False]
    assert [retry(x)[0] for x in xs] == [True, True, False, False, False, False]
    assert retry(xs[1])[2] == 2 and retry(xs[2])[2] == 1 and abs(retry(xs[1])[1] - 1.81) < 1e-9
    assert fallback(xs[3])[1] == DEADLINE
    # logical 1, 2, 3 fail: the breaker opens until 7.0 + 2.0 + 15 and skips 4 and 5
    assert [r[2] for r in breaker(xs)] == [1, 1, 1, 1, 0, 0]
    fb, rt = [fallback(x) for x in xs], [retry(x) for x in xs]
    # the late 200 (logical 3) is billed too
    assert abs(billed(xs, rt) - 3000 * USD) < 1e-15 and abs(billed(xs, fb) - 2000 * USD) < 1e-15
    assert wilson(0, 10)[0] == 0.0 and abs(wilson(5, 10)[1] - 0.7634) < 1e-3
    print("selfcheck ok")


def main(argv: list[str]) -> None:
    if argv[0] == "selfcheck":
        selfcheck()
        return
    rows = [json.loads(line) for p in argv for line in Path(p).read_text().splitlines()]
    print("\n".join(report(rows)))


if __name__ == "__main__":
    main(sys.argv[1:])
