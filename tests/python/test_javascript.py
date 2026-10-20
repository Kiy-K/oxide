"""Native JavaScript facts through the shared ECMAScript pipeline."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from conftest import REPO, write
from test_incremental import apply, dump
from test_typescript import derived, resolved

import oxide
from oxide.capture import capture
from oxide.core.indexing import derive
from oxide.core.languages import python
from oxide.core.languages import typescript as js

FIXTURE = REPO / "fixtures/js_repo"


def test_fixture_structure_and_context(tmp_path: Path, fts: Path) -> None:
    found = derived(FIXTURE)
    files = {f.path: f for f in found.files}
    assert all(f.coverage == "complete" for f in files.values())
    assert files["src/Card.jsx"].language == "javascript"
    assert files["src/typed.ts"].language == "typescript"
    assert "symbol:src/math.mjs#twice" in resolved(found, "calls", "symbol:src/typed.ts#typed")
    assert resolved(found, "calls", "symbol:src/use.cjs#normalize") == {
        "symbol:src/legacy.cjs#positive",
        "symbol:src/legacy.cjs#clamp",
    }
    assert resolved(found, "calls", "symbol:src/Card.jsx#<default>") == {"symbol:src/Card.jsx#Card"}
    assert resolved(found, "tested_by", "symbol:src/Card.jsx#Card") == {
        "symbol:tests/Card.test.jsx#testCard"
    }
    with oxide.open_repository(FIXTURE, data_dir=tmp_path / "data") as repo:
        repo.index()
        result = repo.search("normalize", symbols=["normalize"])
        assert result.entries[0].entity.key == "symbol:src/use.cjs#normalize"
        bundle = repo.build_context("normalize", symbols=["normalize"], budget=1024)
        assert 0 < bundle.used <= 1024
        assert "function normalize" in bundle.payload


def test_modern_syntax_parameters_and_heritage(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.js": "export class Base {}\nexport class Child extends Base {\n"
                "m = (f) => f();\nasync *values() { yield 1; }\n#hidden() {}\n}\n"
                "export async function run({f = () => 1}, ...rest) {\n"
                "f?.(); return rest?.[0] ?? 0; }\n"
                "function f() {}\nexport function* gen() { yield* []; }\n"
            },
        )
    )
    assert found.files[0].coverage == "complete"
    assert resolved(found, "references", "symbol:a.js#Child") == {"symbol:a.js#Base"}
    assert not resolved(found, "calls", "symbol:a.js#run")  # destructured parameter shadows f
    kinds = {e.name: e.kind for e in found.entities if e.category == "symbol"}
    assert kinds["m"] == "method" and kinds["values"] == "method" and kinds["gen"] == "function"
    assert "#hidden" not in kinds


def test_js_ts_exact_paths_collisions_and_barriers(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.js": "export function f() {}",
                "a.ts": "export function f() {}",
                "typed.ts": "export function g() {}",
                "folder/index.jsx": "export function Card() {}",
                "blocked/package.json": '{"main":"index.js"}',
                "blocked/index.js": "export function hidden() {}",
                "use.jsx": 'import {f} from "./a.js"; import {g} from "./typed.js";'
                'import {Card} from "./folder"; import {hidden} from "./blocked";'
                "export function run() { f(); g(); hidden(); return <Card/>; }",
                "ambiguous.js": 'import {f} from "./a"; export function run() { f(); }',
            },
        )
    )
    assert resolved(found, "calls", "symbol:use.jsx#run") == {
        "symbol:a.js#f",
        "symbol:typed.ts#g",
        "symbol:folder/index.jsx#Card",
    }
    assert not resolved(found, "calls", "symbol:ambiguous.js#run")
    assert {"module:javascript:a.js", "module:typescript:a"} <= {e.key for e in found.entities}


@pytest.mark.parametrize(
    "export",
    [
        "module.exports = f;",
        "exports.f = f;",
        "module.exports.f = f;",
        "module.exports = {f};",
        "module.exports = {f: f};",
    ],
)
def test_commonjs_static_exports(tmp_path: Path, export: str) -> None:
    default = export == "module.exports = f;"
    call = "api()" if default else "api.f()"
    found = derived(
        write(
            tmp_path,
            {
                "a.cjs": "function f() {}\n" + export,
                "b.cjs": f'const api = require("./a.cjs"); function run() {{ {call}; }}',
            },
        )
    )
    assert resolved(found, "calls", "symbol:b.cjs#run") == {"symbol:a.cjs#f"}


@pytest.mark.parametrize(
    "suffix",
    [
        "exports.f = other;",
        "const alias = exports;",
        "Object.assign(exports, x);",
        "if (x) exports.f = f;",
        "exports[key] = f;",
        "module.exports = other;",
        "function mutate() { exports.f = f; }",
        "delete exports.f;",
    ],
)
def test_commonjs_mutation_never_invents_exports(tmp_path: Path, suffix: str) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.cjs": "function f() {}\nexports.f = f;\n" + suffix,
                "b.cjs": 'const {f} = require("./a.cjs"); function run() { f(); }',
            },
        )
    )
    assert not resolved(found, "calls", "symbol:b.cjs#run")


@pytest.mark.parametrize(
    "prefix",
    [
        "function require() {}",
        "const require = x;",
        "require = other;",
        "function other(require) {}",
    ],
)
def test_shadowed_require_stays_unresolved(tmp_path: Path, prefix: str) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.cjs": "function f() {}\nmodule.exports = f;",
                "b.cjs": prefix + '\nconst f = require("./a.cjs"); function run() { f(); }',
            },
        )
    )
    assert not resolved(found, "calls", "symbol:b.cjs#run")


def test_coverage_cache_validation_and_dialect_isolation(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "a.js": "",
            "a.ts": "",
            "a.jsx": "",
            "bad.js": "function ok() {}\nfunction broken( {",
            "typed.js": "export const x: number = 1;",
            "latin.js": b"\xff",
        },
    )
    full = derived(root)
    files = {f.path: f for f in full.files}
    assert files["bad.js"].coverage == files["typed.js"].coverage == "partial"
    assert files["latin.js"].coverage == "unsupported"
    assert set(full.facts) == {"javascript", "typescript"}
    for f in capture(root).files:
        parsed = js.parse(f.data, "javascript")
        if not isinstance(parsed, str):
            assert js.decode(json.loads(json.dumps(js.encode(parsed))), len(f.data)) == parsed
    cached = copy.deepcopy(full.facts)
    for key in cached["javascript"]:
        cached["javascript"][key] = ["damaged"]
    assert derive(capture(root), "test-repo", cached) == full


def test_persisted_incremental_equals_full_rebuild(tmp_path: Path) -> None:
    root = write(
        tmp_path / "repo",
        {
            "a.cjs": "function f() {}\nexports.f = f;",
            "use.cjs": 'const {f} = require("./a.cjs"); function run() { f(); }',
            "ui.jsx": 'import {Card} from "./Card.jsx"; export const App = () => <Card/>;',
        },
    )
    steps: list[dict[str, str | bytes | None]] = [
        {"Card.jsx": "export function Card() { return <div/>; }"},
        {"a.cjs": "function f() {}\nexports.f = f;\nexports.f = other;"},
        {"Card.jsx": None, "Card.tsx": "export function Card() { return <div/>; }"},
        {"a.cjs": None},
        {
            "use.cjs": 'const {f} = require("./a.cjs"); function run() { f(); }',
            "a.cjs": "function f() {}\nmodule.exports = {f};",
        },
    ]
    with oxide.open_repository(root, data_dir=tmp_path / "incremental") as repo:
        repo.index()
        for i, edits in enumerate(steps):
            apply(root, edits)
            report = repo.index()
            assert report.reused >= 1
            with oxide.open_repository(root, data_dir=tmp_path / f"full-{i}") as fresh:
                fresh.index()
                assert dump(repo) == dump(fresh)


@pytest.mark.parametrize(
    "assignment,call",
    [
        ("exports.f = function(x) { return x; };", "api.f()"),
        ("exports.f = x => x;", "api.f()"),
        ("module.exports = function(x) { return x; };", "api()"),
        ("module.exports = class { method() {} };", "new api()"),
    ],
)
def test_inline_commonjs_exports(tmp_path: Path, assignment: str, call: str) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.cjs": assignment,
                "b.cjs": f'const api = require("./a.cjs"); function run() {{ {call}; }}',
            },
        )
    )
    suffix = "f" if "exports.f" in assignment else "default"
    assert resolved(found, "calls", "symbol:b.cjs#run") == {f"symbol:a.cjs#<export:{suffix}>"}


@pytest.mark.parametrize(
    "path,package",
    [
        ("a.mjs", "{}"),
        ("a.js", '{"type":"module"}'),
        ("a.js", "invalid"),
    ],
)
def test_cjs_globals_do_not_exist_in_esm_or_unknown_projects(
    tmp_path: Path, path: str, package: str
) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "package.json": package,
                path: 'const f = require("./lib.cjs"); export function run() { f(); }',
                "lib.cjs": "function f() {} module.exports = f;",
            },
        )
    )
    assert not resolved(found, "calls", f"symbol:{path}#run")


def test_javascript_scorer_module_projection(tmp_path: Path) -> None:
    import contextbench_js as scorer

    source = b'import {x} from "./x.js";\nexport const App = () => <X/>;\n'
    assert scorer.module_lines(source) == {1, 3}
    assert scorer.Lines(tmp_path).module("missing.js") == set()
    assert scorer.module_lines(b"exports.f = function() {\n return 1;\n};\n") == {4}


def test_js_module_identity_cannot_collide_with_ts_stems(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.js": "export function f() {}",
                "a.js.ts": "export function f() {}",
                "use.js": 'import * as ns from "./a.js"; export function run() { ns.f(); }',
            },
        )
    )
    assert resolved(found, "calls", "symbol:use.js#run") == {"symbol:a.js#f"}
    assert {"module:javascript:a.js", "module:typescript:a.js"} <= {e.key for e in found.entities}
    assert all("\0" not in e.name for e in found.entities)


def test_javascript_long_chains_keep_parser_hardening() -> None:
    import time

    data = ("const x = p" + ".then(() => { function inner() {} })" * 4000 + ";").encode()
    started = time.perf_counter()
    parsed = js.parse(data, "javascript")
    assert time.perf_counter() - started < 10
    assert isinstance(parsed, js.Parsed)
    assert sum(s.decl is not None and s.decl.name == "inner" for s in parsed.scopes) == 4000
    deep = ("f(" + "()=>{f(" * 20000 + ")}" * 20000 + ");").encode()
    assert js.parse(deep, "javascript") == python.TOO_DEEP


def test_esm_commonjs_interoperability_uses_static_exports(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.cjs": "function f() {} module.exports = {f};",
                "main.mjs": 'import api, {f} from "./a.cjs"; '
                "export function run() { api.f(); f(); }",
                "loader.cjs": 'const api = require("./main.mjs"); function run() { api.run(); }',
            },
        )
    )
    assert resolved(found, "calls", "symbol:main.mjs#run") == {"symbol:a.cjs#f"}
    assert not resolved(found, "calls", "symbol:loader.cjs#run")


def test_package_metadata_changes_resolution_without_reparsing(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "a.cjs": "function f() {} module.exports = f;",
            "use.js": 'const f = require("./a.cjs"); function run() { f(); }',
            "package.json": '{"type":"commonjs"}',
        },
    )
    before = derived(root)
    assert resolved(before, "calls", "symbol:use.js#run") == {"symbol:a.cjs#f"}
    write(root, {"package.json": '{"type":"module"}'})
    after = derive(capture(root), "test-repo", before.facts)
    assert after.reused == 2 and after == derived(root)
    assert not resolved(after, "calls", "symbol:use.js#run")


def test_module_object_is_not_invented_as_a_callable(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.cjs": "function f() {} exports = module.exports = f;",
                "use.cjs": 'const api = require("./a.cjs"); function run() { api(); }',
            },
        )
    )
    assert resolved(found, "imports", "file:use.cjs") == {"module:javascript:a.cjs"}
    assert not resolved(found, "calls", "symbol:use.cjs#run")


def test_reserved_word_jsx_attributes_and_modern_grammar_features() -> None:
    # 0.25.0 accepts the ABI but rejects ordinary JSX class/for attributes.
    source = b"""import x from "./data.json" with { type: "json" };
export class C { static { this.x = 1; } #x = 1; f = () => this.#x; }
export const App = () => <label for="id" class="x" data-id="q" aria-hidden={true}/>;
"""
    parsed = js.parse(source, "javascript")
    assert isinstance(parsed, js.Parsed) and parsed.diagnostics == []


def test_unicode_commonjs_export_facts_round_trip() -> None:
    data = "exports.f\u200c = () => 1;".encode()
    parsed = js.parse(data, "javascript")
    assert isinstance(parsed, js.Parsed) and parsed.diagnostics == []
    assert js.decode(js.encode(parsed), len(data)) == parsed


@pytest.mark.parametrize("export", ["exports.default = f;", "module.exports = {default: f};"])
def test_default_property_is_not_a_commonjs_callable(tmp_path: Path, export: str) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.cjs": "function f() {} " + export,
                "use.cjs": 'const api = require("./a.cjs"); '
                "function run() { api(); api.default(); }",
            },
        )
    )
    assert not resolved(found, "calls", "symbol:use.cjs#run")


def test_methods_and_callback_declarations_do_not_become_lexical_exports(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.js": "const obj = { method() {} };\n"
                "schedule(() => { function hidden() {} });\n"
                "export { hidden };\n"
                "export function run() { method(); hidden(); }\n"
                "export function outer() { function local() {} local(); }",
                "use.js": 'import {hidden} from "./a.js"; export function run() { hidden(); }',
            },
        )
    )
    assert not resolved(found, "calls", "symbol:a.js#run")
    assert not resolved(found, "calls", "symbol:use.js#run")
    assert resolved(found, "calls", "symbol:a.js#outer") == {"symbol:a.js#outer.local"}


def test_block_and_loop_declarations_do_not_leak(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.mjs": "if (flag) { function hidden() {} const arrow = () => 1; }\n"
                "for (let loop = () => 1; flag;) {}\n"
                "export function run() { hidden(); arrow(); loop(); }",
            },
        )
    )
    assert not resolved(found, "calls", "symbol:a.mjs#run")


def test_opaque_class_block_bindings_cannot_select_an_outer_namesake(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.js": "function f() {}\nexport class C { static { function f() { f(); } } }\n"
                "export function run() { f(); }",
            },
        )
    )
    assert not resolved(found, "calls", "symbol:a.js#C.f")
    assert resolved(found, "calls", "symbol:a.js#run") == {"symbol:a.js#f"}


def test_js_interop_rejects_merged_typescript_module_targets(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.ts": "export function own() {}",
                "a/index.ts": "export function other() {}",
                "use.js": 'import {other} from "./a.js"; export function run() { other(); }',
            },
        )
    )
    # TS's legacy logical module groups both files; JS must not invent an exact-file export.
    assert not resolved(found, "calls", "symbol:use.js#run")


@pytest.mark.parametrize("specifier", [".", "./"])
def test_directory_only_specifier_cannot_name_a_sibling_file(
    tmp_path: Path, specifier: str
) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "src.js": "export function wrong() {}",
                "src/index.js": "export function correct() {}",
                "src/use.js": f'import {{wrong}} from "{specifier}"; '
                "export function run() { wrong(); }",
            },
        )
    )
    assert not resolved(found, "calls", "symbol:src/use.js#run")
