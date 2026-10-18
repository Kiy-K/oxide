"""The frozen Rust Phase 3/4 baselines as read-only oracles (docs/phase-3, docs/phase-4).

Retrieval and routing are expected to reproduce the Rust results exactly on the
fixture. Context packing changed format (``oxide-payload-v3``), so only its
quality floor is compared, never payload bytes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURE, REPO

import oxide
from oxide.core import retrieval, routing

BASELINE = json.loads((REPO / "docs/phase-3/baseline-v1/results.json").read_text())


def rust(key: str) -> str:
    """The Rust baseline's entity display: plain paths for files and symbols."""
    return key.split(":", 1)[1] if key.startswith(("file:", "symbol:")) else key


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[oxide.Repository]:
    data = tmp_path_factory.mktemp("baseline-data")
    with oxide.open_repository(FIXTURE, data_dir=data) as repository:
        repository.index()
        yield repository


@pytest.mark.parametrize("task", BASELINE, ids=[t["task"] for t in BASELINE])
def test_entry_points_and_routes_reproduce_phase3(
    repo: oxide.Repository, fts: Path, task: dict[str, Any]
) -> None:
    run = task["runs"]["lexical+structural"]
    view = repo._view()
    hints = retrieval.Hints(symbols=tuple(task["symbol_hints"]))
    found = retrieval.retrieve(view, task["text"], hints)
    mine = [(rust(e.key), e.rank, bool(e.seeds)) for e in found.entries]
    frozen = [
        (e["entity"], e["lexical"][0][0] if e["lexical"] else None, bool(e["seeds"]))
        for e in run["candidate_set"]["entries"]
    ]
    assert mine == frozen
    scores = [e["lexical"][0][1] if e["lexical"] else None for e in run["candidate_set"]["entries"]]
    assert [e.score for e in found.entries] == pytest.approx(scores, rel=1e-12)
    route = routing.route(view, [e.key for e in found.entries])
    assert sorted((rust(c.key), c.depth) for c in route.candidates) == sorted(
        (c["entity"], c["depth"]) for c in run["route"]["candidates"]
    )


def test_context_quality_floor_matches_phase4(repo: oxide.Repository, fts: Path) -> None:
    """Phase 4 config B packed all 9 gold symbols whole at 1,024 units; at 256 units most
    gold fit only as a header (Rust: 0.071 recall)."""
    recall: dict[int, list[float]] = {256: [], 1024: []}
    for task in BASELINE:
        gold = {f"symbol:{g['entity']}" for g in task["gold"]}
        for budget, values in recall.items():
            bundle = repo.build_context(task["text"], budget, symbols=task["symbol_hints"])
            assert bundle.used <= budget
            whole = {e.key for item in bundle.items for e in item.entities if e.complete}
            values.append(len(gold & whole) / len(gold))
    assert sum(recall[1024]) / len(recall[1024]) == 1.0
    assert sum(recall[256]) / len(recall[256]) < 1.0
