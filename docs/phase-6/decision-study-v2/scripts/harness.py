"""Decision Study v2 harness (preregistration.md). No network; src/ is unchanged.

    uv run --with tiktoken python docs/phase-6/decision-study-v2/scripts/harness.py OUT \
        [NAME=JUDGMENTS.json ...] [--stability] [--split heldout --gate GATE.txt]

Reuses the v1 harness (``decision-study-v1/scripts/study.py``: scorer hooks, holder
labels, request files, matched-budget re-packing, heuristic validity assertions) and
replaces its arms with v2's:

  heuristic, oracle_order, oracle_pick   validity (production; graph-v3 ceilings)
  random~order_s0..4                     production's admitted set in a random order
  NAME~order                             the hybrid under test (``hybrid`` below) over the
                                         recorded JEV judgments NAME

``--stability`` adds the stability investigation arms (never gated): each recorded arm
under v1's raw mapping, and over the recorded ``compact`` judgments: ``tie~order_sK``
re-breaks only exact ties; ``Xnoise~order_sK`` and ``Xnoise_sK`` (raw v1 mapping) add
noise resampled from the measured ``compact`` → ``compact-X`` differences.
Every judged arm's packed keys go to OUT/items.json.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2.parent / "decision-study-v1" / "scripts"))

import study  # noqa: E402  (the v1 harness)

from oxide.core import capsule, packing  # noqa: E402

PREREGISTRATION = V2 / "preregistration.md"
STABILITY_SEEDS = range(20)
TIE_JITTER = 0.001  # below JEV's 0.01 grid: reorders exact ties only
items: dict[str, dict[str, dict[str, list[str]]]] = {}  # task -> arm -> budget -> keys
real_pack, real_score, v1_arms = study.real_pack, study.score, study.arms


def valid(p: Any) -> bool:
    return isinstance(p, (int, float)) and not isinstance(p, bool) and 0 <= p <= 1


def hybrid(judgments: dict[str, Any], admitted: dict[str, str | None]) -> Any:
    """The preregistered mapping. ``admitted`` maps each candidate the heuristic values
    at or above the floor to its capsule digest (None: not sendable). If any of them
    lacks a valid judgment the whole request keeps the heuristic (production);
    otherwise admitted candidates get 0.4 + 0.59 * p and the rest abstain."""
    sent = set(admitted.values())

    def judge(caps):  # type: ignore[no-untyped-def]
        if not all(d is not None and valid(judgments.get(d)) for d in sent):
            return {}
        return {
            c.digest: packing.INCLUDE_FLOOR + 0.59 * judgments[c.digest]
            if c.digest in sent
            else None
            for c in caps
        }

    return judge


def noise(base: dict[str, Any], other: dict[str, Any]) -> list[float]:
    """The measured per-candidate differences ``other - base``, in digest order."""
    return [round(other[d] - base[d], 6) for d in sorted(base) if valid(base[d]) and d in other]


def perturbed(base: dict[str, Any], deltas: list[float], seed: int, jitter: bool) -> dict[str, Any]:
    out = {}
    for d, p in base.items():
        rng = random.Random(f"{seed}:{d}")
        if not valid(p):
            out[d] = p
        elif jitter:
            out[d] = min(1.0, p + TIE_JITTER * rng.random())
        else:
            out[d] = min(1.0, max(0.0, p + deltas[rng.randrange(len(deltas))]))
    return out


def arms(task: str, by_key: dict[str, Any], gold: dict[str, int]) -> dict[str, Any]:
    pool, _, source, declarations, _ = study.calls[4096]
    query, derivation = study.state["query"], study.state["derivation"]
    heuristic = {k: packing.value(c)[0] for k, c in by_key.items()}
    admitted: dict[str, str | None] = {}
    for c in pool:  # as ``pack`` asks: has a source and is not a container
        key = c.entity.key
        if c.entity.source is None or not (c.entity.category == "symbol" or key in declarations):
            continue
        if heuristic[key] >= packing.INCLUDE_FLOOR:
            built = capsule.build(query, derivation, c, source)
            admitted[key] = None if built is None else built.digest

    full = v1_arms(task, by_key, gold)
    out = {k: full[k] for k in ("heuristic", "oracle_order", "oracle_pick")}

    def shuffled(seed: int):  # type: ignore[no-untyped-def]
        def judge(caps):  # type: ignore[no-untyped-def]
            return {
                c.digest: packing.INCLUDE_FLOOR
                + 0.59 * random.Random(f"{seed}:{c.digest}").random()
                if heuristic[c.key] >= packing.INCLUDE_FLOOR
                else None
                for c in caps
            }

        return judge

    out |= {f"random~order_s{s}": shuffled(s) for s in study.SEEDS}
    for name, data in study.recorded.items():
        out[f"{name}~order"] = hybrid(data["judgments"], admitted)
    if "--stability" in sys.argv and "compact" in study.recorded:
        out |= {n: full[n] for n in study.recorded}  # v1's raw mapping, for comparison
        base = study.recorded["compact"]["judgments"]
        for s in STABILITY_SEEDS:
            out[f"tie~order_s{s}"] = hybrid(perturbed(base, [], s, True), admitted)
        for ctl in ("repeat", "reversed"):
            if f"compact-{ctl}" not in study.recorded:
                continue
            deltas = noise(base, study.recorded[f"compact-{ctl}"]["judgments"])
            for s in STABILITY_SEEDS:
                values = perturbed(base, deltas, s, False)
                out[f"{ctl}noise~order_s{s}"] = hybrid(values, admitted)
                out[f"{ctl}noise_s{s}"] = capsule.Recorded(values)  # v1's raw mapping
    return out


def pack(pool, scopes, budget, source, declarations, judge=None):  # type: ignore[no-untyped-def]
    found = real_pack(pool, scopes, budget, source, declarations, judge)
    if judge is not None:
        task = items.setdefault(study.state["task"], {})
        task.setdefault(judge.name, {})[str(budget)] = [e.key for i in found[0] for e in i.entities]
    return found


def score(repo, lines, task):  # type: ignore[no-untyped-def]
    study.state["task"] = task["id"]
    return real_score(repo, lines, task)


def main(argv: list[str]) -> None:
    if "--split" in argv and argv[argv.index("--split") + 1] != "dev":
        gate = Path(argv[argv.index("--gate") + 1]) if "--gate" in argv else None
        text = gate.read_text() if gate else ""
        digest = "sha256:" + hashlib.sha256(PREREGISTRATION.read_bytes()).hexdigest()
        assert "DEV GATE: PASS" in text and digest in text, "held-out is sealed"
    # v1's main installs its module globals ``score``/``pack`` as hooks and re-packs
    # through ``real_pack``; route all three through v2.
    study.arms, study.real_pack, study.score = arms, pack, score
    study.main([a for a in argv if a != "--stability"])
    (Path(argv[0]) / "items.json").write_text(json.dumps(items) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
