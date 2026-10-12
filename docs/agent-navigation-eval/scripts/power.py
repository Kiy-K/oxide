#!/usr/bin/env python3
"""Paired power / sensitivity analysis for a follow-up navigation eval (follow-up.md § Task 3).

The unit is the task (seeds averaged within a task, then paired across arms), as in the
frozen analyze.py. For a paired task-level difference d_i:

  Var(d_i) = s_tau^2 + 2 * s_w^2 / k

  s_w^2    seed (within task x arm) Bernoulli variance, averaged over cells
  s_tau^2  variance of the task-specific effect (effect heterogeneity)
  k        seeds per (task, arm)

Required tasks n = ((z_{1-a/2} + z_{1-b}) / MDE)^2 * Var(d_i). s_w^2 is estimated from the
frozen held-out rows and from noisier scenarios; the alternative's s_tau^2 comes from a
"concentrated effect" model: a share f of tasks gain g each, so MDE = f*g and
s_tau^2 = g^2 f (1-f). A Monte Carlo check (paired t over tasks) validates the formula.

  power.py   (reads results/heldout-runs.json, prints JSON)
"""
import json
import math
import random
import statistics as st
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
Z = {0.05: 1.959964, 0.025: 2.241403}  # two-sided alpha: one comparison; Bonferroni over A and B
Z_POWER = 0.841621  # 80%


def cells(rows):
    c = defaultdict(list)
    for r in rows:
        c[(r["instance_id"], r["variant"])].append(1.0 if r["resolved"] else 0.0)
    return c


def components(rows):
    c = cells(rows)
    k = st.mean(len(v) for v in c.values())
    s_w2 = st.mean(st.variance(v) for v in c.values() if len(v) > 1)  # unbiased within-cell
    tasks = sorted({t for t, _ in c})
    out = {"tasks": len(tasks), "seeds": k, "s_w2": round(s_w2, 4),
           "always_solved": sum(all(x == 1 for v in "ABCE" for x in c[(t, v)]) for t in tasks),
           "never_solved": sum(all(x == 0 for v in "ABCE" for x in c[(t, v)]) for t in tasks), "pairs": {}}
    for x, y in (("C", "A"), ("E", "A"), ("C", "B"), ("E", "B")):
        d = [st.mean(c[(t, x)]) - st.mean(c[(t, y)]) for t in tasks]
        out["pairs"][f"{x}-{y}"] = {"mean": round(st.mean(d), 4), "var_d": round(st.variance(d), 4),
                                    "s_tau2_est": round(max(0.0, st.variance(d) - 2 * s_w2 / k), 4)}
    return out


def n_tasks(mde, s_tau2, s_w2, k, alpha):
    return math.ceil(((Z[alpha] + Z_POWER) / mde) ** 2 * (s_tau2 + 2 * s_w2 / k))


def simulate(n, k, f, g, p, alpha, sims=2000, seed=7):
    """Power of a paired one-sided-at-alpha/2 t-test over tasks; a share f of tasks gains g."""
    rng = random.Random(seed)
    hits = 0
    for _ in range(sims):
        d = []
        for _ in range(n):
            q = min(1.0, p + g) if rng.random() < f else p
            d.append(sum(rng.random() < q for _ in range(k)) / k - sum(rng.random() < p for _ in range(k)) / k)
        hits += st.mean(d) / ((st.stdev(d) or 1e-9) / math.sqrt(n)) > Z[alpha]
    return hits / sims


def main():
    rows = json.loads((HERE / "results/heldout-runs.json").read_text())
    comp = components(rows)
    # Seed-noise scenarios: observed (near-deterministic SWE-bench Verified tasks), and strata
    # where many tasks sit mid-range (mean p(1-p) over tasks).
    s_w2_grid = {"observed": comp["s_w2"], "moderate": 0.10, "high": 0.20}
    designs = []
    for mde, f, g in ((0.05, 0.25, 0.20), (0.10, 0.25, 0.40), (0.10, 0.50, 0.20), (0.15, 0.30, 0.50)):
        s_tau2 = g * g * f * (1 - f)
        for name, s_w2 in s_w2_grid.items():
            for k in (1, 2, 3):
                designs.append({"mde": mde, "share_moved": f, "gain_per_moved_task": g, "seed_noise": name,
                                "s_w2": s_w2, "s_tau2": round(s_tau2, 4), "seeds": k,
                                "tasks_alpha_0.05": n_tasks(mde, s_tau2, s_w2, k, 0.05),
                                "tasks_alpha_0.025": n_tasks(mde, s_tau2, s_w2, k, 0.025)})
    # Smallest success difference the frozen design (19 tasks, 3 seeds) detects with 80% power.
    mde19 = {p: round((Z[0.05] + Z_POWER) * math.sqrt(v["var_d"] / comp["tasks"]), 3)
             for p, v in comp["pairs"].items()}
    # Self-check: formula vs simulation, mid-range tasks (p = 0.5, s_w2 = 0.25), f 0.25, g 0.4, k 2.
    n = n_tasks(0.10, 0.4 * 0.4 * 0.25 * 0.75, 0.25, 2, 0.05)
    sim = simulate(n, 2, 0.25, 0.40, 0.5, 0.05)
    assert 0.72 <= sim <= 0.90, f"formula vs simulation disagree: n={n} power={sim}"
    by_v = defaultdict(list)
    for r in rows:
        by_v[r["variant"]].append(r)
    cost = {v: {"input_tokens_mean": round(st.mean(r["input_tokens"] for r in rs)),
                "uncached_input_mean": round(st.mean(r["uncached_input_tokens"] for r in rs)),
                "output_tokens_mean": round(st.mean(r["output_tokens"] + r["reasoning_output_tokens"] for r in rs)),
                "wall_s_mean": round(st.mean(r["wall_s"] for r in rs), 1)} for v, rs in sorted(by_v.items())}
    print(json.dumps({"observed": comp, "mde_frozen_design_80pct": mde19,
                      "simulation_check": {"design": "mde 0.10, f 0.25, g 0.40, p 0.5, k 2",
                                           "tasks": n, "power": sim},
                      "designs": designs, "per_run_cost_heldout": cost}, indent=1))


if __name__ == "__main__":
    main()
