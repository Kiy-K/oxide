"""Shell slice conformance (docs/shell.md): dialect, facts, nesting, resolution, evidence."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REPO
from test_typescript import derived, resolved

from oxide.capture import capture
from oxide.core.indexing import dialect, source_dialect, validate
from oxide.core.languages import shell, shell_facts

FIXTURE = REPO / "fixtures/shell_repo"


def _write(root: Path, name: str, source: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)


def test_dialect_and_shebang_detection() -> None:
    assert dialect("a.sh") == "sh"
    assert dialect("a.bash") == dialect("a.bats") == "bash"
    assert dialect("a.bashrc") == "bash"
    assert source_dialect("run", b"#!/usr/bin/env bash\n") == "bash"
    assert source_dialect("run", b"#!/bin/sh\n") == "sh"
    assert source_dialect("a.sh", b"#!/bin/bash\n") == "bash"
    assert source_dialect("a.py", b"#!/usr/bin/env bash\n") == "python"
    assert source_dialect("run", b"echo hi\n") is None


def test_declarations_and_nested_constructs() -> None:
    source = b"""#!/usr/bin/env bash
X=1
export Y=two
declare -r Z=3
outer() {
  local name="$1"
  if [ -n "$name" ]; then for f in a b; do inner "$f"; done; fi
  while read -r line; do echo "$line"; done
  case "$name" in a) inner a ;; *) choose ;; esac
  value=$(inner b)
  ( cd /tmp && inner c )
  echo "$value" | grep x
}
inner() { echo inner; }
choose() { :; }
outer arg
"""
    parsed = shell.parse(source, "bash")
    assert not isinstance(parsed, str)
    functions = {d.name: d for d in parsed.decls if d.kind == "function"}
    assert set(functions) == {"outer", "inner", "choose"}
    local = next(d for d in parsed.decls if d.name == "name")
    assert local.local and local.owner is not None
    assert parsed.decls[local.owner].name == "outer"
    assert ("inner", "command") in {(u.name, u.kind) for u in parsed.uses if u.owner is not None}
    assert ("value", "variable") in {(u.name, u.kind) for u in parsed.uses}
    assert all(0 <= u.start <= u.end <= len(source) for u in parsed.uses)


def test_plain_assignment_inside_function_is_file_level(tmp_path: Path) -> None:
    parsed = shell.parse(b'f() { X=1; local Y=2; echo "$X"; }\n', "bash")
    assert not isinstance(parsed, str)
    declarations = {d.name: d for d in parsed.decls}
    assert declarations["X"].owner is None and not declarations["X"].local
    assert declarations["Y"].owner is not None and declarations["Y"].local
    _write(tmp_path, "a.sh", '#!/usr/bin/env bash\nf() { X=1; }\necho "$X"\n')
    found = derived(tmp_path)
    assert resolved(found, "references", "file:a.sh") == {"symbol:a.sh#X"}


def test_quoted_dynamic_interpreter_path_is_an_unresolved_call(tmp_path: Path) -> None:
    _write(tmp_path, "a.sh", '#!/usr/bin/env bash\nbash "$S"\nsh -c "$CMD"\n')
    found = derived(tmp_path)
    calls = [e for e in found.edges if e.kind == "calls" and e.source == "file:a.sh"]
    assert len(calls) == 1
    assert calls[0].resolution == "unresolved" and calls[0].name == '"$S"'


def test_coercing_cache_facts_are_rejected() -> None:
    parsed = shell.parse(b"x=1\n", "bash")
    assert not isinstance(parsed, str)
    bad = shell_facts.encode(parsed)
    bad[1][0][0] = []  # kind is not a string
    with pytest.raises(ValueError):
        shell_facts.decode(bad, 4, "bash")
    bad[1][0][0] = "variable"
    bad[2] = [[[], 0, 1, None, "command", ""]]  # use kind is not a string
    with pytest.raises(ValueError):
        shell_facts.decode(bad, 4, "bash")


def test_functions_defined_in_nested_constructs_are_captured() -> None:
    parsed = shell.parse(
        b"run() { if true; then nested() { echo hi; }; fi; for x in a; do other() { :; }; done; }",
        "bash",
    )
    assert not isinstance(parsed, str)
    assert {d.name for d in parsed.decls if d.kind == "function"} == {"run", "nested", "other"}
    assert all(d.owner is None for d in parsed.decls if d.kind == "function")


def test_static_and_dynamic_source_paths() -> None:
    parsed = shell.parse(
        b'source ./lib/a.sh\n. ../b.sh\nsource lib.sh\nsource "$X/a.sh"\n'
        b"source ${X}/a.sh\nsource /abs/a.sh\nbash ./run.sh\nbash -e ./run.sh\n",
        "bash",
    )
    assert not isinstance(parsed, str)
    got = {u.name: (u.kind, u.path) for u in parsed.uses if u.kind != "command"}
    assert got["./lib/a.sh"] == ("source", "./lib/a.sh")
    assert got["../b.sh"] == ("source", "../b.sh")
    assert got["lib.sh"] == ("dynamic_source", "")
    assert got['"$X/a.sh"'] == ("dynamic_source", "")
    assert got["${X}/a.sh"] == ("dynamic_source", "")
    assert got["/abs/a.sh"] == ("dynamic_source", "")
    assert got["./run.sh"] == ("script", "./run.sh")


def test_bash_only_syntax_in_posix_is_partial() -> None:
    parsed = shell.parse(b"#!/bin/sh\nA=(1 2)\n[[ -f x ]] && (( i++ ))\n", "sh")
    assert not isinstance(parsed, str)
    assert parsed.diagnostics
    assert any("Bash-only" in d for d in parsed.diagnostics)
    found = derived(FIXTURE)
    info = next(f for f in found.files if f.path == "tools/bashism.sh")
    assert info.language == "sh" and info.coverage == "partial" and info.diagnostics
    posix = next(f for f in found.files if f.path == "tools/posix.sh")
    assert posix.language == "sh" and posix.coverage == "complete"


def test_facts_codec_roundtrip() -> None:
    for source in (
        b'#!/bin/bash\nx=1\nf() { local y=2; echo "$y"; }\nsource ./a.sh\n',
        b"#!/bin/sh\ng() { :; }\n",
    ):
        for dialect_name in ("bash", "sh"):
            parsed = shell.parse(source, dialect_name)
            assert not isinstance(parsed, str)
            encoded = shell_facts.encode(parsed)
            assert shell_facts.decode(encoded, len(source), dialect_name) == parsed
            with pytest.raises(ValueError):
                shell_facts.decode(["garbage"], len(source), dialect_name)
            with pytest.raises(ValueError):
                shell_facts.decode(encoded, len(source), "sh" if dialect_name == "bash" else "bash")


def test_cross_file_resolution_follows_source_chain(tmp_path: Path) -> None:
    _write(tmp_path, "main.sh", "#!/usr/bin/env bash\nsource ./lib/a.sh\nhelper\n")
    _write(tmp_path, "lib/a.sh", "#!/usr/bin/env bash\nsource ./b.sh\nhelper() { :; }\n")
    _write(tmp_path, "lib/b.sh", "#!/usr/bin/env bash\nhelper2() { :; }\n")
    found = derived(tmp_path)
    assert resolved(found, "imports", "file:main.sh") == {"file:lib/a.sh"}
    assert resolved(found, "imports", "file:lib/a.sh") == {"file:lib/b.sh"}
    assert resolved(found, "calls", "file:main.sh") == {"symbol:lib/a.sh#helper"}


def test_unsourced_same_name_is_not_proven(tmp_path: Path) -> None:
    _write(tmp_path, "main.sh", "#!/usr/bin/env bash\nhelper\n")
    _write(tmp_path, "other.sh", "#!/usr/bin/env bash\nhelper() { :; }\n")
    found = derived(tmp_path)
    assert "symbol:other.sh#helper" in {e.key for e in found.entities}
    assert resolved(found, "calls", "file:main.sh") == set()


def test_two_sourced_definitions_are_ambiguous(tmp_path: Path) -> None:
    _write(tmp_path, "main.sh", "#!/usr/bin/env bash\nsource ./a.sh\nsource ./b.sh\nhelper\n")
    _write(tmp_path, "a.sh", "#!/usr/bin/env bash\nhelper() { :; }\n")
    _write(tmp_path, "b.sh", "#!/usr/bin/env bash\nhelper() { :; }\n")
    found = derived(tmp_path)
    calls = [e for e in found.edges if e.kind == "calls" and e.source == "file:main.sh"]
    assert len(calls) == 1
    assert calls[0].resolution == "ambiguous"
    assert set(calls[0].targets) == {"symbol:a.sh#helper", "symbol:b.sh#helper"}


def test_dynamic_source_is_unresolved_not_false_proven(tmp_path: Path) -> None:
    _write(tmp_path, "main.sh", '#!/usr/bin/env bash\nD=./lib\nsource "$D/a.sh"\nsource plain.sh\n')
    _write(tmp_path, "lib/a.sh", "#!/usr/bin/env bash\nhelper() { :; }\n")
    _write(tmp_path, "plain.sh", "#!/usr/bin/env bash\nhelper() { :; }\n")
    found = derived(tmp_path)
    imports = [e for e in found.edges if e.kind == "imports" and e.source == "file:main.sh"]
    assert imports and all(e.resolution == "unresolved" for e in imports)
    assert {e.name for e in imports} == {'"$D/a.sh"', "plain.sh"}


def test_script_invocation_resolves_to_captured_file() -> None:
    found = derived(FIXTURE)
    assert resolved(found, "calls", "symbol:scripts/run-task.sh#run_all") >= {
        "file:scripts/helper.sh",
        "symbol:scripts/run-task.sh#helper_name",
    }
    assert resolved(found, "imports", "file:tests/test_entry.bats") == {"file:lib/util.sh"}


def test_variable_resolution_is_file_local(tmp_path: Path) -> None:
    _write(tmp_path, "a.sh", '#!/usr/bin/env bash\nX=1\nf() { echo "$X"; }\n')
    _write(tmp_path, "b.sh", "#!/usr/bin/env bash\nX=2\n")
    found = derived(tmp_path)
    assert resolved(found, "references", "symbol:a.sh#f") == {"symbol:a.sh#X"}


def test_fixture_structure() -> None:
    found = derived(FIXTURE)
    validate(found)
    assert sum(e.category == "symbol" for e in found.entities) > 20
    assert any(f.path.endswith(".bats") and f.language == "bash" for f in found.files)
    assert any(f.path.endswith(".md") and f.coverage == "unsupported" for f in found.files)
    bats = [
        e.name
        for e in found.edges
        if e.kind == "calls" and e.source == "file:tests/test_entry.bats"
    ]
    assert not ({"}", "@test"} & set(bats))


def test_bash_n_oracle_acceptance() -> None:
    for file in capture(FIXTURE).files:
        if not file.path.endswith(".sh"):
            continue  # .bats uses @test syntax that bash -n does not accept
        check = subprocess.run(["bash", "-n", str(FIXTURE / file.path)], capture_output=True)
        assert check.returncode == 0, check.stderr.decode()
        parsed = shell.parse(file.data, "bash")  # bash -n accepts the Bash superset
        assert not isinstance(parsed, str) and not parsed.diagnostics


def _shellcheck() -> list[str] | None:
    for command in (
        ["shellcheck"],
        ["mise", "exec", "shellcheck@0.11.0", "--", "shellcheck"],
    ):
        if shutil.which(command[0]) is None:
            continue
        if subprocess.run([*command, "--version"], capture_output=True).returncode == 0:
            return command
    return None


def test_shellcheck_source_reference() -> None:
    """ShellCheck is an independent lint reference, not a graph oracle."""
    command = _shellcheck()
    if command is None:
        pytest.skip("shellcheck not installed")
    target = FIXTURE / "scripts/run-task.sh"
    report = subprocess.run(
        [*command, "-f", "json1", "-s", "bash", str(target)], capture_output=True
    )
    codes = {c["code"] for c in json.loads(report.stdout or b"{}").get("comments", [])}
    assert {1090, 1091} & codes  # the two source paths are runtime-dependent
