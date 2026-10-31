"""JEV-assisted ordering of admitted candidates (ADR-0009 § 1): the policy Decision
Study v3 confirmed, as a ``DecisionProvider`` over a ``CapsuleJudge``.

Production's heuristic admits: the admitted set is every asked candidate whose
``packing.value`` reaches ``INCLUDE_FLOOR``. The judge only orders it: each admitted
candidate gets ``INCLUDE_FLOOR + SCALE * p``, so it stays admitted, and every other
candidate abstains and keeps its value below the floor. Fallback is all or nothing:
unless every admitted candidate was sent and validly judged, ``judge`` raises and
``packing.judged`` keeps the heuristic for the whole request, so two scales never mix.

Deterministic policy only; the adapter that reaches a model lives outside ``core``.
``outcome`` records how the request ended (``judged`` or a fallback reason, ADR-0009 § 2).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from oxide.core.capsule import CapsuleJudge, build
from oxide.core.packing import INCLUDE_FLOOR, Candidate, value
from oxide.models import SourceRef

POLICY = "oxide-order-admitted-v1"
SCALE = 0.59  # INCLUDE_FLOOR + SCALE * p spans [0.40, 0.99]


class Unanswered(Exception):
    """An adapter's failure, named by its outcome (``deadline``, ``http 529``, …)."""

    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome


def _valid(p: object) -> bool:
    return isinstance(p, (int, float)) and not isinstance(p, bool) and 0 <= p <= 1


@dataclass(slots=True)
class AdmittedOrder:
    """A request-scoped provider. ``manifest`` identifies the adapter's configuration
    (model, renderer, questions, deadline, retries) for the bundle's ``versions``."""

    capsules: CapsuleJudge
    manifest: str
    outcome: str | None = None

    @property
    def identity(self) -> str:
        return self.capsules.identity

    @property
    def versions(self) -> dict[str, str]:
        return {"order": POLICY, "judge": self.identity, "judge_manifest": self.manifest}

    def judge(self, candidates: Sequence[Candidate]) -> Mapping[str, float | None]:
        admitted = [c for c in candidates if value(c)[0] >= INCLUDE_FLOOR]
        judged: dict[str, float] = {}
        try:
            found = self.capsules.judge(candidates)
            for c in admitted:
                p = found.get(c.entity.key)
                if c.entity.key not in found:
                    j = self.capsules
                    sendable = build(j.query, j.derivation, c, j.source) is not None
                    raise Unanswered("incomplete" if sendable else "unsendable")
                if not _valid(p):
                    raise Unanswered("unparsable")
                judged[c.entity.key] = INCLUDE_FLOOR + SCALE * float(p)  # type: ignore[arg-type]
        except Unanswered as error:
            self.outcome = error.outcome
            raise
        except TimeoutError:
            self.outcome = "deadline"
            raise
        except Exception as error:
            self.outcome = f"error ({type(error).__name__})"
            raise
        self.outcome = "judged"
        return {c.entity.key: judged.get(c.entity.key) for c in candidates}


Order = Callable[[str, str, Callable[[SourceRef], bytes]], AdmittedOrder]
"""Builds a request's provider from its query, derivation id and source reader."""
