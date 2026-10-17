"""Incremental indexing: reused parse facts derive exactly what a full rebuild does."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURE, write

import oxide
from oxide.capture import capture, digest
from oxide.core import python, typescript
from oxide.core.indexing import derive, dialect, validate

Edits = dict[str, str | bytes | None]
BASE: dict[str, str | bytes] = {
    "pkg/__init__.py": "",
    "pkg/a.py": "def helper():\n    pass\n",
    "pkg/b.py": "from pkg.a import helper\nfrom pkg.d import later\n\n"
    "def use():\n    helper()\n    later()\n",
    "tests/test_b.py": "from pkg.b import use\n\ndef test_use():\n    use()\n",
    "README.md": "# demo\n",
}
# Each step edits the tree; most change what *other*, unchanged files resolve to.
STEPS: list[tuple[str, Edits]] = [
    ("rename the imported symbol", {"pkg/a.py": "def helper2():\n    pass\n"}),
    ("add the module an import names", {"pkg/d.py": "def later():\n    pass\n"}),
    (
        "rebind names importers resolve",
        {
            "pkg/d.py": "def later():\n    pass\ndef reset():\n    global later\n"
            "def now():\n    pass\nnow = 1\n"
        },
    ),
    (
        "import the rebound names",
        {"pkg/e.py": "from pkg.d import later, now\nclass E(Exception):\n    pass\n"},
    ),
    ("delete a resolved module", {"pkg/a.py": None}),
    ("move a file: same bytes, new path", {"pkg/b.py": None, "pkg/bb.py": BASE["pkg/b.py"]}),
    ("turn the test file into a non-test", {"tests/test_b.py": None, "tests/b.py": "x = 1\n"}),
    ("partial and undecodable sources", {"pkg/bad.py": "def broken(:\n", "pkg/raw.py": b"\xff"}),
    (
        "back to the first tree",
        {
            **dict.fromkeys(
                ("pkg/d.py", "pkg/e.py", "pkg/bb.py", "tests/b.py", "pkg/bad.py", "pkg/raw.py")
            ),
            **BASE,
        },
    ),
]


def apply(root: Path, edits: Edits) -> None:
    for name, content in edits.items():
        if content is None:
            (root / name).unlink()
        else:
            write(root, {name: content})


def dump(repo: oxide.Repository) -> dict[str, Any]:
    """Every logical fact of the current generation, with the random RepoId masked."""
    view = repo.store.view()
    keys = [row[0] for row in view._query("MATCH (e:Entity) RETURN e.key ORDER BY e.key")]
    entities = view.entities(keys)
    return {
        "snapshot": (view.snapshot.snapshot_id, view.snapshot.derivation_id),
        "files": repo.files(),
        "entities": {k: e if k != "repo" else None for k, e in entities.items()},
        "relations": {k: repo.relations(k) for k in keys},
    }


def test_facts_round_trip_through_their_encoding() -> None:
    sources = [f.read_bytes() for f in sorted(FIXTURE.rglob("*.py"))]
    sources.append(
        b"from . import *\nglobal g\ndef f(a, *b, c=1, **d):\n    (x := 1)\nclass C(B, m.N): pass\n"
    )
    sources.append(b"def broken(:\n")
    for data in sources:
        parsed = python.parse(data)
        assert not isinstance(parsed, str)
        assert python.decode(json.loads(json.dumps(python.encode(parsed))), len(data)) == parsed


def test_reused_facts_derive_what_a_full_derivation_does(tmp_path: Path) -> None:
    root = write(tmp_path / "repo", BASE)
    previous = derive(capture(root), "r")
    for step, edits in STEPS:
        apply(root, edits)
        snapshot = capture(root)
        incremental = derive(snapshot, "r", previous.facts)
        assert incremental == derive(snapshot, "r"), step
        previous = incremental


def test_incremental_index_equals_a_full_rebuild(tmp_path: Path) -> None:
    root = write(tmp_path / "repo", BASE)
    with oxide.open_repository(root, data_dir=tmp_path / "incremental") as repo:
        first = repo.index()
        assert (first.previous, first.parsed, first.reused) == (None, 4, 0)
        for i, (step, edits) in enumerate(STEPS):
            apply(root, edits)
            report = repo.index()
            with oxide.open_repository(root, data_dir=tmp_path / f"full-{i}") as fresh:
                assert fresh.index().reused == 0
                assert dump(repo) == dump(fresh), step
            assert report.status == "built" and report.previous is not None, step
            python_files = sum(f.path.endswith(".py") for f in repo.files())
            assert report.parsed + report.reused == python_files, step
        # Back to the first tree: pkg/b.py's bytes were last seen as pkg/bb.py.
        assert report.added == ("pkg/a.py", "pkg/b.py", "tests/test_b.py")
        assert report.modified == ()
        assert report.deleted == (
            "pkg/bad.py",
            "pkg/bb.py",
            "pkg/d.py",
            "pkg/e.py",
            "pkg/raw.py",
            "tests/b.py",
        )
        assert (report.parsed, report.reused) == (2, 2)


def test_the_report_names_changes_and_reuse(repo_copy: Path, private_data: Path) -> None:
    with oxide.open_repository(repo_copy) as repo:
        repo.index()
        write(repo_copy, {"oxidepy/retry.py": "def changed():\n    pass\n", "new.py": "x = 1\n"})
        (repo_copy / "oxidepy" / "cache.py").unlink()
        lines: list[str] = []
        report = repo.index(progress=lines.append)
        assert (report.added, report.modified, report.deleted) == (
            ("new.py",),
            ("oxidepy/retry.py",),
            ("oxidepy/cache.py",),
        )
        assert (report.parsed, report.reused) == (2, 6)
        assert lines[0].startswith("capturing") and lines[-1].startswith("publishing")
        unchanged = repo.index()
        assert unchanged.status == "unchanged"
        assert (unchanged.added, unchanged.parsed, unchanged.reused) == ((), 0, 0)


@pytest.mark.parametrize("damage", ["corrupt", "missing", "unsealed"])
def test_damaged_or_absent_facts_cost_a_reparse_never_correctness(
    repo_copy: Path, private_data: Path, damage: str
) -> None:
    oxide.index_repository(repo_copy)
    (generation,) = private_data.glob("repos/*/generations/*")
    facts = generation / "facts.json"
    if damage == "corrupt":  # still valid JSON, but not what was sealed
        facts.write_bytes(facts.read_bytes() + b" ")
    elif damage == "missing":
        facts.unlink()
    else:  # a generation sealed before facts existed
        manifest = json.loads((generation / "MANIFEST.json").read_text())
        del manifest["facts"]
        (generation / "MANIFEST.json").write_text(json.dumps(manifest))
    write(repo_copy, {"new.py": "x = 1\n"})
    with oxide.open_repository(repo_copy) as repo:
        report = repo.index()
        assert report.reused == 0
        expected = dump(repo)
    shutil.rmtree(private_data)
    with oxide.open_repository(repo_copy) as fresh:
        fresh.index()
        assert dump(fresh) == expected


# Every kind of fact parse produces: nesting, decorators, bases, imports, calls, rebinding.
FACTS_SOURCE = """\
import os.path as p
from .. import sibling
from pkg.mod import *
global g

@decorator
class Outer(Base, m.Mixin):
    def method(self, a, *b, c=1):
        def inner():
            helper()
        return p.join(a)
"""
BAD: list[Any] = [None, -1, 1, 10**9, True, "x", "a.b", [], {}]


def _paths(value: Any, at: tuple[int, ...] = ()) -> list[tuple[int, ...]]:
    """Every position in an encoded entry, containers included."""
    if not isinstance(value, list):
        return [at]
    return [at, *(p for i, v in enumerate(value) for p in _paths(v, (*at, i)))]


def _replace(value: Any, at: tuple[int, ...], new: Any) -> Any:
    if not at:
        return new
    out = copy.deepcopy(value)
    holder = out
    for i in at[:-1]:
        holder = holder[i]
    holder[at[-1]] = new
    return out


def test_unsound_cached_facts_are_parsed_never_trusted_or_fatal(tmp_path: Path) -> None:
    snapshot = capture(write(tmp_path / "repo", {"pkg/m.py": FACTS_SOURCE}))
    full = derive(snapshot, "r")
    ((key, good),) = full.facts["python"].items()
    size = len(snapshot.files[0].data)
    rejected = 0
    for at in _paths(good):
        for bad in BAD:
            entry = _replace(good, at, bad)
            try:
                python.decode(entry, size)
            except ValueError:
                rejected += 1
                derived = derive(snapshot, "r", {"python": {key: entry}})
                assert derived == full and derived.reused == 0, (at, bad)
                continue
            # Well-formed facts parse could have produced: they may differ, never fail.
            validate(derive(snapshot, "r", {"python": {key: entry}}))
    assert rejected > len(_paths(good)) * len(BAD) // 2


def test_sealed_but_unsound_facts_are_parsed_again(repo_copy: Path, private_data: Path) -> None:
    oxide.index_repository(repo_copy)
    (generation,) = private_data.glob("repos/*/generations/*")
    facts = json.loads((generation / "facts.json").read_bytes())
    victim = next(k for k, v in sorted(facts["python"].items()) if len(v[0]) > 1)
    facts["python"][victim][0][1][0] = 1  # a declaration as its own parent: a cycle
    raw = json.dumps(facts).encode()
    (generation / "facts.json").write_bytes(raw)
    manifest = json.loads((generation / "MANIFEST.json").read_text())
    manifest["facts"]["digest"] = digest(raw)  # the seal matches: only decode can tell
    (generation / "MANIFEST.json").write_text(json.dumps(manifest))
    write(repo_copy, {"new.py": "x = 1\n"})
    lines: list[str] = []
    with oxide.open_repository(repo_copy) as repo:
        report = repo.index(progress=lines.append)
        assert (report.parsed, report.reused) == (2, len(facts["python"]) - 1)
        assert "1 cached parse facts were unusable and parsed again" in lines
        expected, healed = dump(repo), repo.store.view().facts()
    shutil.rmtree(private_data)
    with oxide.open_repository(repo_copy) as fresh:
        fresh.index()
        assert dump(fresh) == expected
        assert fresh.store.view().facts() == healed  # the bad entry did not carry over


# TypeScript: the same guarantees, over edits that change what barrels, re-exports
# and namespace imports in *other*, unchanged files resolve to.
TS_BASE: dict[str, str | bytes] = {
    "src/index.ts": 'export * from "./a";\nexport { b as renamed } from "./b";\n',
    "src/a.ts": "export function helper() {}\n",
    "src/b.ts": 'import { helper } from "./a";\nimport * as d from "./d.js";\n'
    "export function b() {\n  helper();\n  d.later();\n}\n",
    "src/App.tsx": 'import { renamed } from "./index";\n'
    "export const App = () => <Box onClick={renamed} />;\nfunction Box() { return null; }\n",
    "src/b.test.ts": 'import { renamed } from ".";\nit("b", () => renamed());\n',
    "lib/util.py": "def py():\n    pass\n",
}
TS_STEPS: list[tuple[str, Edits]] = [
    ("rename what a barrel re-exports", {"src/a.ts": "export function helper2() {}\n"}),
    ("add the module a namespace import names", {"src/d.ts": "export const later = () => 1;\n"}),
    ("rebind an exported name", {"src/d.ts": "export let later = () => 1;\nlater = () => 2;\n"}),
    (
        "star-export a package from the barrel",
        {
            "src/index.ts": 'export * from "./a";\nexport * from "pkg";\n'
            'export { b as renamed } from "./b";\n'
        },
    ),
    ("delete a re-exported module", {"src/b.ts": None}),
    ("move a file: same bytes, new path", {"src/a.ts": None, "src/lib/a.ts": TS_BASE["src/a.ts"]}),
    ("same bytes in another dialect", {"src/App.tsx": None, "src/App.ts": TS_BASE["src/App.tsx"]}),
    ("turn the test file into a non-test", {"src/b.test.ts": None, "src/check.ts": "x();\n"}),
    (
        "partial and undecodable sources",
        {"src/bad.ts": "export function bad( {\n", "src/raw.ts": b"\xff"},
    ),
    (
        "back to the first tree",
        {
            **dict.fromkeys(
                (
                    "src/d.ts",
                    "src/lib/a.ts",
                    "src/App.ts",
                    "src/check.ts",
                    "src/bad.ts",
                    "src/raw.ts",
                )
            ),
            **TS_BASE,
        },
    ),
]


def test_typescript_facts_round_trip_through_their_encoding() -> None:
    sources = [f.read_bytes() for f in sorted((FIXTURE.parent / "ts_repo").rglob("*.ts*"))]
    sources += [TS_FACTS_SOURCE.encode(), b"export function broken( {\n"]
    for data in sources:
        for language in ("typescript", "tsx"):
            parsed = typescript.parse(data, language)
            assert not isinstance(parsed, str)
            encoded = json.loads(json.dumps(typescript.encode(parsed)))
            assert typescript.decode(encoded, len(data)) == parsed


def test_typescript_reused_facts_derive_what_a_full_derivation_does(tmp_path: Path) -> None:
    root = write(tmp_path / "repo", TS_BASE)
    previous = derive(capture(root), "r")
    for step, edits in TS_STEPS:
        apply(root, edits)
        snapshot = capture(root)
        incremental = derive(snapshot, "r", previous.facts)
        assert incremental == derive(snapshot, "r"), step
        previous = incremental


def test_typescript_incremental_index_equals_a_full_rebuild(tmp_path: Path) -> None:
    root = write(tmp_path / "repo", TS_BASE)
    with oxide.open_repository(root, data_dir=tmp_path / "incremental") as repo:
        first = repo.index()
        assert (first.parsed, first.reused) == (6, 0)
        for i, (step, edits) in enumerate(TS_STEPS):
            apply(root, edits)
            report = repo.index()
            with oxide.open_repository(root, data_dir=tmp_path / f"full-{i}") as fresh:
                assert fresh.index().reused == 0
                assert dump(repo) == dump(fresh), step
            sources = sum(dialect(f.path) is not None for f in repo.files())
            assert report.parsed + report.reused == sources, step
            assert report.parsed <= len(report.added) + len(report.modified), step


# Every kind of fact TypeScript parse produces: declarations of each kind, exports,
# re-exports, imports, heritage, calls, JSX-free bindings and rebinding.
TS_FACTS_SOURCE = """\
import def, { a as b } from "./m.js";
import * as ns from "../n";
import "./side";
export { b as c } from "./r";
export * from "./s";
export * as q from "./q";
export { b as d };
export default b;
export class C<T> extends ns.Base<T> implements I {
  m({ k, z: w }: O, ...rest: R[]) { helper(k); }
}
export const f = (x: number) => x, v = 1;
namespace N { export function inner() {} }
interface I extends J {}
let counter = 0;
counter++;
"""


def test_unsound_typescript_facts_are_parsed_never_trusted_or_fatal(tmp_path: Path) -> None:
    snapshot = capture(write(tmp_path / "repo", {"src/m.ts": TS_FACTS_SOURCE}))
    full = derive(snapshot, "r")
    ((key, good),) = full.facts["typescript"].items()
    size = len(snapshot.files[0].data)
    rejected = 0
    for at in _paths(good):
        for bad in BAD:
            entry = _replace(good, at, bad)
            try:
                typescript.decode(entry, size)
            except ValueError:
                rejected += 1
                derived = derive(snapshot, "r", {"typescript": {key: entry}})
                assert derived == full and derived.reused == 0, (at, bad)
                continue
            validate(derive(snapshot, "r", {"typescript": {key: entry}}))
    assert rejected > len(_paths(good)) * len(BAD) // 2
