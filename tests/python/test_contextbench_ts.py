"""ContextBench-ts (protocol in ``docs/phase-6/typescript-v1/README.md``, harness in
``contextbench_ts.py``): scorer regression cases and the task protocol, which always
run, and the gate over the frozen TypeScript baseline, which needs its 81 pinned
corpora and runs only with ``OXIDE_CONTEXTBENCH_TS=1`` (``mise run contextbench-ts``).

As in ``test_contextbench.py``, quality, invariants and output compatibility are
separate tests.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import contextbench as cb
import contextbench_ts as ts
import pytest
from conftest import FTS, write

import oxide
from oxide.storage.fts import fts_unavailable

GATE = os.environ.get("OXIDE_CONTEXTBENCH_TS") == "1"
gate = pytest.mark.skipif(not GATE, reason="TypeScript gate: OXIDE_CONTEXTBENCH_TS=1")


# --- scorer regression cases ---

MODULE = b"""import { a } from "./a";
const LIMIT = 3;
export const policy = {
  retries: LIMIT,
};
@decorate
class C {
  m() {}
}
const helper = () => 2, n = 1;
describe("x", () => {
  const inner = () => 3;
  expect({
    get self() {
      return 1;
    },
    "str"() {},
  }).ok;
});
export default class {}
export const { GET } = api;
"""


def test_module_lines_follow_the_symbol_rules() -> None:
    # Exported or function-valued variables, decorated classes, declarations inside
    # test callbacks and object-literal methods are symbols; string-named members,
    # destructured exports and plain statements are module-level.
    assert ts.module_lines(MODULE, "m.ts") == {1, 2, 11, 13, 17, 18, 19, 21, 22}


def test_module_lines_parse_tsx_as_tsx() -> None:
    source = b"const x = <div />;\nexport const App = () => <Button />;\n"
    assert ts.module_lines(source, "a.tsx") == {1, 3}


def test_fixture_gold_is_the_grammar_span() -> None:
    root = cb.REPO / "fixtures" / "ts_repo"
    client, retry = "src/net/client.ts", "src/net/retry.ts"
    assert ts.gold_span(root, f"{client}#ApiClient.request") == (client, 12, 27)
    assert ts.gold_span(root, f"{retry}#defaultRetryPolicy") == (retry, 24, 24)
    module = "src/ui/__tests__/Button.test.tsx"
    assert ts.gold_span(root, f"{module}#{module}:__module__") == (module, 1, 18)
    with pytest.raises(ValueError):
        ts.gold_span(root, f"{client}#ApiClient.missing")


def test_oracle_resolves_only_by_declared_path_rules() -> None:
    files = [
        "packages/shared/src/index.ts",
        "packages/runtime/src/a.ts",
        "src/vs/base/common/strings.ts",
        "src/x/index.tsx",
        "lib/strings.ts",
    ]
    oracle = ts.Oracle(files, {"@vue/shared": "packages/shared", "rt": "packages/runtime"})
    importer = "packages/runtime/src/b.ts"
    assert oracle.resolve(importer, "@vue/shared") == ("workspace", {files[0]})
    assert oracle.resolve(importer, "rt/a") == ("workspace", {files[1]})
    assert oracle.resolve(importer, "vs/base/common/strings") == ("alias", {files[2]})
    assert oracle.resolve("src/a.ts", "./x") == ("relative", {files[3]})
    assert oracle.resolve("src/a.ts", "./missing.js") == ("relative", set())
    assert oracle.resolve(importer, "strings") == ("external", set())  # no "/": a package
    assert oracle.resolve(importer, "react") == ("external", set())


def test_unresolved_import_names_keep_their_specifier() -> None:
    assert ts.specifier_of("./a:foo") == "./a"
    assert ts.specifier_of("./a") == "./a"
    assert ts.specifier_of("@vue/shared:isArray") == "@vue/shared"
    assert ts.specifier_of("node:fs:readFile") == "node:fs"


def test_v2_corpora_add_only_the_project_files() -> None:
    kept = ["tsconfig.json", "src/tsconfig.base.json", "a/b.tsconfig.json"]
    assert [ts.is_config(p) for p in kept] == [True, True, False]
    assert not ts.is_config("packages/tsconfig/base.json") and not ts.is_config("jsconfig.json")


def test_numbered_annotations_lose_only_their_line_numbers() -> None:
    # Some Multi-SWE-bench gold is in `cat -n` form; an empty last line loses its tab.
    assert ts.unnumbered("7\tdef f():\n8\t  return 1\n9", 7) == ("def f():\n  return 1\n", True)
    assert ts.unnumbered("7\tdef f():\n9\t  return 1", 7) == ("7\tdef f():\n9\t  return 1", False)
    assert ts.unnumbered("def f():\n  return 1", 1) == ("def f():\n  return 1", False)


def test_lost_spans_get_a_structural_cause(tmp_path: Path, fts: Path) -> None:
    root = write(
        tmp_path / "repo",
        {
            "packages/a/src/index.ts": "export function lowerCaseFolding(t: string) {\n"
            "  return t;\n}\n",
            "packages/b/src/main.ts": 'import { lowerCaseFolding } from "@x/a";\n'
            "export function searchTerms(q: string) {\n  return lowerCaseFolding(q);\n}\n",
            "packages/c/src/other.ts": "export function unrelated() {\n  return 1;\n}\n",
            "packages/c/src/broken.ts": "export function damaged( {\n  return 1;\n}\n",
        },
    )
    files = ("packages/a/src/index.ts", "packages/c/src/other.ts", "packages/c/src/broken.ts")
    gold = [{"file": f, "start": 1, "end": 3, "class": "exact", "verified": True} for f in files]
    task = {"id": "t", "split": "dev", "corpus": "c", "query": "searchTerms", "symbols": []}
    with oxide.open_repository(root, data_dir=tmp_path / "data") as repo:
        repo.index()
        view = repo._view()
        stats, extra = ts.import_facts(view, ts.Oracle(list(view.files), {"@x/a": "packages/a"}))
        assert stats["unresolved_imports"] == {"workspace:found": 1}
        result = cb.score(repo, ts.Lines(root), {**task, "gold": gold})
        found = repo.search("searchTerms")
        ts.attribute(view, result, [e.entity.key for e in found.entries], extra)
    causes = [g.get("cause") for g in result["gold"]]
    assert causes == ["unresolved-import", "no-path", "partial-file"]


# --- the task protocol (no corpora needed) ---


def test_tasks_and_audit_are_the_frozen_protocol() -> None:
    tasks = json.loads(ts.TASKS.read_text())
    audit = json.loads(ts.AUDIT.read_text())
    assert (tasks["version"], audit["version"]) == (ts.TASKS_VERSION, ts.AUDIT_VERSION)
    assert audit["tasks"].endswith(cb._sha(ts.TASKS.read_bytes()))
    fixture = cb.REPO / "fixtures" / "ts_repo"
    for task in tasks["tasks"]:
        assert task["source"].startswith(("fixtures/", "ContextBench full.parquet#"))
        assert all(set(g) <= {"file", "start", "end", "sha256", "symbol"} for g in task["gold"])
        if task["source"].startswith("fixtures/"):  # gold recomputed from the grammar
            spans = [(g["file"], g["start"], g["end"]) for g in task["gold"]]
            assert [ts.gold_span(fixture, g["symbol"]) for g in task["gold"]] == spans
        else:  # a verdict on every annotated span, TypeScript gold in every task
            verdicts = [(v["file"], v["start"], v["end"]) for v in audit["spans"][task["id"]]]
            assert verdicts == [(g["file"], g["start"], g["end"]) for g in task["gold"]]
            assert any(ts.is_source(g["file"]) for g in task["gold"])
            repository = task["corpus"].split("@")[0].replace("__", "/")
            assert task["split"] == ("heldout" if repository in ts.HELDOUT else "dev")


def test_outside_gold_is_kept_apart_never_scored() -> None:
    audit = json.loads(ts.AUDIT.read_text())["spans"]
    for task in ts.load_tasks()["tasks"]:
        classes = [v["class"] for v in audit.get(task["id"], [])]
        assert len(task["outside"]) == classes.count("outside")
        assert all(ts.is_source(g["file"]) for g in task["gold"])
        assert not any(ts.is_source(o["file"]) for o in task["outside"])


# --- the gate over the frozen baseline (opt-in) ---


@pytest.fixture(scope="module")
def current() -> dict[str, Any]:
    reason = fts_unavailable(FTS)
    assert reason is None, f"{reason} (the gate needs the pinned extension)"
    with tempfile.TemporaryDirectory(prefix="oxide-contextbench-ts-") as data:
        try:
            return ts.evaluate(ts.load_tasks(), Path(data))
        except FileNotFoundError as error:
            pytest.fail(str(error))


def _frozen(name: str) -> Any:
    return cb.load(name, ts.BASELINE)


@gate
def test_protocol_is_the_frozen_one() -> None:
    manifest = _frozen("manifest.json")
    assert (manifest["tolerance"], manifest["gated"]) == (cb.TOLERANCE, list(cb.GATED))
    assert manifest["budgets"] == list(cb.BUDGETS) and manifest["scorer"] == ts.SCORER
    assert manifest["tasks"].endswith(cb._sha(ts.TASKS.read_bytes()))
    assert manifest["audit"].endswith(cb._sha(ts.AUDIT.read_bytes()))


@gate
def test_corpora_are_the_frozen_ones(current: dict[str, Any]) -> None:
    frozen = _frozen("manifest.json")["corpora"]
    keys = ("url", "commit", "ts_files", "tree_sha256")
    for name, corpus in current["corpora"].items():
        assert {k: corpus.get(k) for k in keys} == {k: frozen[name].get(k) for k in keys}, (
            f"corpus {name} changed: its results are not comparable, not a quality result"
        )


@gate
def test_bundles_are_bounded_and_deterministic(current: dict[str, Any]) -> None:
    results = current["results"]
    assert [r["task"] for r in results] == [r["task"] for r in _frozen("results.json")]
    for result in results:
        for budget, bundle in result["bundles"].items():
            assert bundle["within_budget"], f"{result['task']}@{budget}: over budget"
            assert bundle["deterministic"], f"{result['task']}@{budget}: not repeatable"


@gate
def test_gold_integrity_failures_stay_visible(current: dict[str, Any]) -> None:
    audit = json.loads(ts.AUDIT.read_text())["spans"]
    classes = Counter(v["class"] for spans in audit.values() for v in spans)
    unverified = {c: n for c, n in classes.items() if c not in (*cb.VERIFIED, "outside")}
    results = current["results"]
    scored = Counter(g["class"] for r in results for g in r["gold"] if not g["verified"])
    assert scored == unverified
    assert sum(len(r["outside"]) for r in results) == classes["outside"]


@gate
@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_quality_does_not_regress(current: dict[str, Any], split: str) -> None:
    now = ts.aggregate(current["results"])[split]["verified"]
    frozen = _frozen("metrics.json")[split]["verified"]
    assert (now["tasks"], now["spans"]) == (frozen["tasks"], frozen["spans"])
    regressions = [
        f"{m}: {frozen[m]:.4f} -> {now[m]:.4f}"
        for m in cb.GATED
        if now[m] < frozen[m] - cb.TOLERANCE
    ]
    assert not regressions, f"{split} regressed beyond {cb.TOLERANCE}:\n" + "\n".join(regressions)


@gate
def test_output_is_compatible(current: dict[str, Any]) -> None:
    frozen = {r["task"]: r["compat"] for r in _frozen("results.json")}
    versions = current["results"][0]["versions"]
    expected = _frozen("manifest.json")["versions"]
    if versions != expected:
        bumped = {k: (expected.get(k), v) for k, v in versions.items() if expected.get(k) != v}
        pytest.fail(f"versioned output change, not a quality verdict: {bumped}")
    changed = []
    for result in current["results"]:
        was = frozen[result["task"]]
        if diff := [k for k, v in result["compat"].items() if was.get(k) != v]:
            changed.append(f"{result['task']}: {', '.join(diff)}")
    assert not changed, "output changed without a version bump:\n" + "\n".join(changed)
