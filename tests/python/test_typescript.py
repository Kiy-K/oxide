"""TypeScript slice conformance (docs/typescript.md): identity, kinds, ES-module
resolution, JSX, test discovery, coverage, and context over the fixture."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import REPO, write

import oxide
from oxide.capture import capture
from oxide.core import tsconfig, typescript
from oxide.core.indexing import Derived, derive, validate

TS_FIXTURE = REPO / "fixtures" / "ts_repo"


def derived(root: Path) -> Derived:
    result = derive(capture(root), "test-repo")
    validate(result)
    return result


def resolved(result: Derived, kind: str, source: str) -> set[str]:
    return {
        t
        for e in result.edges
        if e.kind == kind and e.source == source and e.resolution == "resolved"
        for t in e.targets
    }


def test_fixture_structure() -> None:
    found = derived(TS_FIXTURE)
    entities = {e.key: e for e in found.entities}
    assert (len(found.entities), len(found.edges)) == (53, 134)
    retry = "symbol:src/net/retry.ts#"
    assert entities[retry + "RetryPolicy"].kind == "interface"
    assert entities[retry + "RetryPolicy.backoffMs"].kind == "method"
    assert entities[retry + "defaultRetryPolicy"].kind == "variable"
    assert entities["module:typescript:src"].name == "src"  # src/index.ts
    # Implementors and callers (fixtures/structural_benchmark.json), across files.
    implementors = {e.source for e in found.edges if e.kind == "references"}
    assert implementors == {
        retry + "ExponentialBackoff",
        "symbol:src/net/policies.ts#LinearBackoff",
        "symbol:src/net/policies.ts#NoRetryPolicy",
    }
    callers = {
        e.source for e in found.edges if e.kind == "calls" and retry + "shouldRetry" in e.targets
    }
    assert callers == {
        "symbol:src/net/policies.ts#attemptWithPolicy",
        "symbol:src/net/client.ts#ApiClient.request",
    }
    # The barrel's re-exports name declarations; ``export *`` names the module.
    assert resolved(found, "imports", "file:src/index.ts") == {
        "module:typescript:src/auth/service",
        "module:typescript:src/cache/versioned_store",
        "symbol:src/net/client.ts#ApiClient",
        retry + "RetryPolicy",
        retry + "ExponentialBackoff",
        retry + "defaultRetryPolicy",
    }
    # JSX use is a call; the test file tests what it calls.
    button = "symbol:src/ui/Button.tsx#Button"
    test_file = "file:src/ui/__tests__/Button.test.tsx"
    assert button in resolved(found, "calls", test_file)
    assert resolved(found, "tested_by", button) == {test_file}
    files = {f.path: f for f in found.files}
    assert files["src/ui/Button.tsx"].language == "typescript"
    assert files["tsconfig.json"].coverage == "unsupported"


DECLARATIONS = """\
export function over(a: string): string;
export function over(a: any) { return a; }
export default class {}
@sealed
export class Shape<T> extends Base<T> implements Area {
  @logged() get size() { return 1; }
  set size(v: number) {}
  #hidden() {}
  abstract area(): number;
  onClick = () => this.size;
  constructor(private readonly n: number) { super(); }
}
export const make = (x: number) => new Shape(), other = 1;
namespace Outer.Inner { export function deep() {} }
declare module "pkg" { export function ambient(): void; }
export interface Area { area(): number; }
export type Id = string;
export enum Level { Low }
let plain = 1;
"""


def test_declarations_get_nested_ordinal_identities_and_kinds(tmp_path: Path) -> None:
    root = write(tmp_path, {"src/d.ts": DECLARATIONS})
    entities = {e.key.removeprefix("symbol:src/d.ts#"): e for e in derived(root).entities}
    kinds = {k: e.kind for k, e in entities.items() if e.category == "symbol"}
    assert kinds == {
        "over": "function",
        "over~1": "function",  # overloads are separate declaration sites
        "<default>": "class",
        "Shape": "class",
        "Shape.size": "method",
        "Shape.size~1": "method",
        "Shape.area": "method",
        "Shape.onClick": "method",
        "Shape.constructor": "method",
        "make": "function",
        "other": "variable",
        "Outer": "namespace",  # namespace Outer.Inner declares Outer
        "Outer.deep": "function",
        "<module>": "namespace",
        "<module>.ambient": "function",
        "Area": "interface",
        "Area.area": "method",
        "Id": "type",
        "Level": "enum",
    }
    shape, size = entities["Shape"], entities["Shape.size"]
    assert shape.signature == "export class Shape<T> extends Base<T> implements Area"
    assert size.signature == "get size()"
    assert shape.source is not None and size.source is not None
    data = DECLARATIONS.encode()
    # Ranges start at decorators; signatures are the header past them.
    assert data[shape.source.start :].startswith(b"@sealed\nexport class")
    assert data[size.source.start :].startswith(b"@logged()")
    assert entities["make"].signature == "make = (x: number) =>"  # one of two declarators
    assert entities["Id"].signature == "export type Id"
    assert entities["Level"].signature == "export enum Level"


USE = """\
import { pad, renamed, add } from "./util/index.js";
import main, * as math from "./util/math";
import { fromA, fromB, missing } from "./a";
import { own, useState } from "./external";
import { later } from "./rebound";
import def from "./util";
import "./side-effect";

export function run(pad2: number) {
  main();
  pad();
  renamed();
  add();
  math.helper();
  math.nope();
  fromA();
  fromB();
  own();
  useState();
  later();
  def();
  local();
  this.method();
  function local() {}
}

export function shadowed(pad: () => void) {
  pad();
}

export class K {
  method() {}
  call() {
    method();
  }
}
"""
RESOLUTION = {
    "src/util/index.ts": 'export * from "./math";\nexport * from "./text";\n'
    'export { helper as renamed } from "./math";\n',
    "src/util/math.ts": "export function add() {}\nexport function helper() {}\n"
    "export default function main() {}\n",
    "src/util/text.ts": "export function pad() {}\nexport function add() {}\n",
    "src/a.ts": 'export * from "./b";\nexport function fromA() {}\n',
    "src/b.ts": 'export * from "./a";\nexport function fromB() {}\n',
    "src/external.ts": 'export * from "react";\nexport function own() {}\n',
    "src/rebound.ts": "export function later() {}\nlater = () => {};\n",
    "src/use.ts": USE,
}


def test_resolution_follows_the_module_graph(tmp_path: Path) -> None:
    found = derived(write(tmp_path, RESOLUTION))
    data = USE.encode()

    def target(call: str, source: str = "run") -> tuple[str, ...] | str:
        """Where the call ``call(`` made in ``source`` resolves to."""
        (edge,) = [
            e
            for e in found.edges
            if e.kind == "calls"
            and e.source == f"symbol:src/use.ts#{source}"
            and e.evidence is not None
            and data[e.evidence.start : e.evidence.end] == f"{call}()".encode()
        ]
        return edge.targets if edge.resolution != "unresolved" else "unresolved"

    math, text = "symbol:src/util/math.ts#", "symbol:src/util/text.ts#"
    assert target("main") == (math + "main",)  # a default import
    assert target("pad") == (text + "pad",)  # through ``export *`` and a .js specifier
    assert target("renamed") == (math + "helper",)  # ``export { a as b } from``
    assert target("add") == (math + "add", text + "add")  # two stars: ambiguous
    assert target("math.helper") == (math + "helper",)  # a namespace member
    assert target("math.nope") == "unresolved"
    assert target("fromA") == ("symbol:src/a.ts#fromA",)  # a star cycle terminates
    assert target("fromB") == ("symbol:src/b.ts#fromB",)
    assert target("own") == ("symbol:src/external.ts#own",)
    assert target("useState") == "unresolved"  # may come from a package star
    assert target("later") == "unresolved"  # rebound where it is defined
    assert target("def") == "unresolved"  # ``export *`` never re-exports a default
    assert target("local") == ("symbol:src/use.ts#run.local",)
    assert target("this.method") == "unresolved"
    assert target("pad", "shadowed") == "unresolved"  # a parameter shadows the import
    assert target("method", "K.call") == "unresolved"  # members are not bare names
    imports = {
        (e.name, e.targets)
        for e in found.edges
        if e.kind == "imports" and e.source == "file:src/use.ts"
    }
    assert ("./side-effect", ()) in imports  # an unknown module stays unresolved
    assert ("./a:missing", ()) in imports
    assert (None, ("module:typescript:src/util/math",)) in imports


PROJECT = {
    # A vscode-like project: an extended base sets baseUrl and paths (JSONC).
    "src/tsconfig.base.json": """{
  // comments and trailing commas are tsconfig syntax
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "vs/*": ["./vs/*"],
      "vs/exact": ["./vs/base/strings"],
      "@x/*": ["./missing/*", "./lib/*",],
      "js/*": ["./js/*", "./vs/*"], /* the first lookup finds JavaScript */
    },
  },
}""",
    "src/tsconfig.json": '{"extends": "./tsconfig.base", "include": ["./vs", "./lib"],'
    ' "exclude": ["**/*.skip.ts"]}',
    "src/vs/base/strings.ts": "export function lower() {}\n",
    "src/vs/thing.ts": "export function thing() {}\n",
    "src/js/thing.js": "export function thing() {}\n",
    "src/lib/util/index.ts": "export function util() {}\n",
    "src/vs/app.ts": 'import { lower } from "vs/base/strings";\n'
    'import { lower as exact } from "vs/exact";\nimport { util } from "@x/util";\n'
    'import { util as viaBase } from "lib/util";\nimport { thing } from "js/thing";\n'
    'import { missing } from "vs/missing";\nimport { useState } from "react";\n',
    "src/vs/app.skip.ts": 'import { lower } from "vs/base/strings";\n',  # excluded
    "src/scripts/tool.ts": 'import { lower } from "vs/base/strings";\n',  # not included
    # paths without baseUrl are relative to their config; a nearer config wins.
    "packages/p/tsconfig.json": '{"compilerOptions": {"paths": {"#/*": ["./src/*"]}}}',
    "packages/p/src/a.ts": 'import { b } from "#/b";\nimport { lower } from "vs/base/strings";\n',
    "packages/p/src/b.ts": "export function b() {}\n",
    # An extends TypeScript would read from a package: unknowable, resolves nothing.
    "other/tsconfig.json": '{"extends": "@tsconfig/node16/tsconfig.json",'
    ' "compilerOptions": {"baseUrl": "."}}',
    "other/a.ts": 'import { c } from "c";\n',
    "other/c.ts": "export function c() {}\n",
}


def test_bare_specifiers_resolve_through_their_project_paths(tmp_path: Path) -> None:
    found = derived(write(tmp_path, PROJECT))

    def imports(path: str) -> dict[str, tuple[str, ...] | str]:
        return {
            (e.name or "") if e.resolution == "unresolved" else e.targets[0]: e.resolution
            for e in found.edges
            if e.kind == "imports" and e.source == f"file:{path}"
        }

    strings, util = "symbol:src/vs/base/strings.ts#lower", "symbol:src/lib/util/index.ts#util"
    assert imports("src/vs/app.ts") == {
        strings: "resolved",  # vs/* and the exact vs/exact both name it
        util: "resolved",  # @x/*: the first substitution is missing, the second exists
        "js/thing:thing": "unresolved",  # TypeScript stops at the JavaScript file
        "vs/missing:missing": "unresolved",
        "react:useState": "unresolved",
    }
    assert imports("src/vs/app.skip.ts") == {"vs/base/strings:lower": "unresolved"}
    assert imports("src/scripts/tool.ts") == {"vs/base/strings:lower": "unresolved"}
    assert imports("packages/p/src/a.ts") == {
        "symbol:packages/p/src/b.ts#b": "resolved",
        "vs/base/strings:lower": "unresolved",  # another project's paths do not apply
    }
    assert imports("other/a.ts") == {"c:c": "unresolved"}


def test_tsconfig_reading_follows_typescript() -> None:
    assert tsconfig.jsonc(b'{"a": "//not a comment", /* c */ "b": [1,],}') == {
        "a": "//not a comment",
        "b": [1],
    }
    with pytest.raises(ValueError):
        tsconfig.jsonc(b"{")
    options = tsconfig.Options("src", (("a/*", ("x/*",)), ("a/b/*", ("y/*", "z/*"))), "src")
    assert tsconfig.candidates(options, "a/b/c") == ["src/y/c", "src/z/c", "src/a/b/c"]
    escapes = tsconfig.Options(".", (("up/*", ("../*",)),), ".")
    assert tsconfig.candidates(escapes, "up/a") == ["up/a"]  # ../a is outside the repository
    projects = tsconfig.Projects(
        {
            "tsconfig.json": b'{"files": ["only.ts"], "compilerOptions": {"baseUrl": "."}}',
            "a/tsconfig.json": b'{"extends": "./b.json"}',
            "a/b.json": b'{"extends": "./tsconfig.json"}',  # a cycle
        }
    )
    assert projects.options("only.ts") == tsconfig.Options(".", (), ".")
    assert projects.options("other.ts") is None  # files without include: only those
    assert projects.options("a/x.ts") is None


def test_jsx_components_are_calls_and_intrinsics_are_not(tmp_path: Path) -> None:
    files = {
        "ui/Card.tsx": "export const Card = () => <div />;\n"
        "export function Title() { return null; }\n",
        "ui/App.tsx": 'import { Card } from "./Card";\nimport * as UI from "./Card";\n'
        "export function App() {\n  return <section><Card /><UI.Title>x</UI.Title></section>;\n}\n",
    }
    found = derived(write(tmp_path, files))
    calls = [e for e in found.edges if e.kind == "calls" and e.source == "symbol:ui/App.tsx#App"]
    assert sorted(t for e in calls for t in e.targets) == [
        "symbol:ui/Card.tsx#Card",
        "symbol:ui/Card.tsx#Title",
    ]
    assert len(calls) == 2  # <section> is not a call


def test_test_files_are_discovered_by_name_and_directory(tmp_path: Path) -> None:
    files = {
        "src/lib.ts": "export function f() {}\n",
        "src/lib.test.ts": 'import { f } from "./lib";\nit("works", () => f());\n',
        "src/__tests__/helpers.ts": 'import { f } from "../lib";\nexport function g() { f(); }\n',
    }
    found = derived(write(tmp_path, files))
    entities = {e.key: e for e in found.entities}
    assert entities["symbol:src/__tests__/helpers.ts#g"].test
    assert not entities["symbol:src/lib.ts#f"].test
    assert resolved(found, "tested_by", "symbol:src/lib.ts#f") == {
        "file:src/lib.test.ts",
        "symbol:src/__tests__/helpers.ts#g",
    }
    assert typescript.is_test_file("a/b.spec.tsx") and not typescript.is_test_file("a/spec.ts")


def test_coverage_is_explicit_for_partial_unsupported_and_declaration_files(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "broken.ts": "export function ok() {}\nexport function bad( {\n",
            "latin.ts": b"const x = '\xe9';\n",
            "types.d.ts": "export declare function typed(): void;\n",
            "script.js": "function js() {}\n",
        },
    )
    found = derived(root)
    files = {f.path: f for f in found.files}
    assert (files["broken.ts"].coverage, files["latin.ts"].coverage) == ("partial", "unsupported")
    assert files["broken.ts"].diagnostics and files["latin.ts"].language == "typescript"
    assert (files["types.d.ts"].coverage, files["script.js"].coverage) == (
        "complete",
        "unsupported",
    )
    entities = {e.key for e in found.entities}
    assert {"symbol:broken.ts#ok", "symbol:types.d.ts#typed", "module:typescript:types"} <= entities


def test_module_names_follow_the_path_convention() -> None:
    assert typescript.module_name("src/a.ts") == "src/a"
    assert typescript.module_name("src/a.d.ts") == "src/a"
    assert typescript.module_name("src/ui/index.tsx") == "src/ui"
    assert typescript.module_name("index.ts") is None
    assert typescript.module_name("src/a.js") is None
    assert typescript.dialect("a.mts") == "typescript" and typescript.dialect("a.tsx") == "tsx"


def test_python_and_typescript_coexist(tmp_path: Path) -> None:
    # Identical bytes in three dialects: facts are cached per dialect.
    root = write(tmp_path, {"pkg/__init__.py": "", "pkg/index.ts": "", "pkg/v.tsx": ""})
    full = derived(root)
    assert set(full.facts) == {"python", "typescript", "tsx"}
    again = derive(capture(root), "test-repo", full.facts)
    assert again == full and again.reused == 3
    assert {"module:python:pkg", "module:typescript:pkg"} <= {e.key for e in full.entities}


# The legacy TypeScript fixture tasks (fixtures/benchmark.json and
# structural_benchmark.json): every gold entity is packed at 1,024 units.
LEGACY = [
    q
    for name in ("benchmark.json", "structural_benchmark.json")
    for q in json.loads((REPO / "fixtures" / name).read_text())["queries"]
    if q["repo"] == "ts"
]


@pytest.fixture(scope="module")
def ts_repo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[oxide.Repository]:
    data = tmp_path_factory.mktemp("ts-data")
    with oxide.open_repository(TS_FIXTURE, data_dir=data) as repository:
        repository.index()
        yield repository


@pytest.mark.parametrize("task", LEGACY, ids=[q["id"] for q in LEGACY])
def test_legacy_fixture_tasks_pack_their_gold(
    ts_repo: oxide.Repository, fts: Path, task: dict[str, str]
) -> None:
    gold = {
        f"file:{g.split('#')[0]}" if g.endswith(":__module__") else f"symbol:{g}"
        for g in task["relevant"]
    }
    bundle = ts_repo.build_context(task["text"], 1024)
    assert bundle.used <= 1024 and bundle == ts_repo.build_context(task["text"], 1024)
    assert gold <= {e.key for item in bundle.items for e in item.entities}
