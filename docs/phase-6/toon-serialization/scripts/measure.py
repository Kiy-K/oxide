"""Deterministic measurements (preregistration.md § Metrics): no model is called.

    measure.py conformance   official toon-format/spec fixtures -> results/conformance.json
    measure.py sizes         DATA/cases.json -> results/sizes.json (tokens, bytes, units,
                             round trip, MCP equality, truncation detection, encode time)
    measure.py package       installed size, files, dependencies and import time

Run with ``uv run --with toon-format --with tiktoken``.
"""

from __future__ import annotations

import importlib.metadata
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import tiktoken
import toon_format
from fastmcp.tools.base import default_serializer
from pydantic import TypeAdapter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build import DATA, envelope, render  # noqa: E402

from oxide.core.packing import units  # noqa: E402
from oxide.models import SearchResult  # noqa: E402

RESULTS = Path(__file__).resolve().parents[1] / "results"
SPEC = DATA / "spec"
ENCODINGS = {name: tiktoken.get_encoding(name) for name in ("o200k_base", "cl100k_base")}
RUNS = 50


def conformance() -> None:
    commit = subprocess.run(
        ["git", "-C", str(SPEC), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    rows = []
    for path in sorted((SPEC / "tests/fixtures").glob("*/*.json")):
        fixture = json.loads(path.read_text())
        for test in fixture["tests"]:
            options = test.get("options") or {}
            try:
                if fixture["category"] == "encode":
                    kwargs: dict[str, Any] = {}
                    if "delimiter" in options:
                        kwargs["delimiter"] = options["delimiter"]
                    if "indentSize" in options:
                        kwargs["indent_size"] = options["indentSize"]
                    got: Any = toon_format.encode(test["input"], **kwargs)
                else:
                    kwargs = {"strict": options.get("strict", True)}
                    if "indentSize" in options:
                        kwargs["indent_size"] = options["indentSize"]
                    got = toon_format.decode(test["input"], **kwargs)
                ok = not test.get("shouldError") and got == test["expected"]
                error = None
            except Exception as exc:  # noqa: BLE001 - the fixture decides what is an error
                ok, got, error = bool(test.get("shouldError")), None, f"{type(exc).__name__}: {exc}"
            rows.append({
                "file": f"{fixture['category']}/{path.name}", "name": test["name"],
                "section": test.get("specSection"), "ok": ok,
                "options": options, "error": error,
                "got": None if ok else got, "expected": None if ok else test.get("expected"),
            })  # fmt: skip
    failed = [r for r in rows if not r["ok"]]
    report = {
        "spec_commit": commit,
        "toon_format": importlib.metadata.version("toon-format"),
        "tests": len(rows),
        "passed": len(rows) - len(failed),
        "failed": failed,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "conformance.json").write_text(json.dumps(report, indent=1) + "\n")
    print(f"{report['passed']}/{report['tests']} fixtures pass ({commit[:12]})")
    for r in failed:
        print(" FAIL", r["file"], r["section"], r["name"], r["error"] or "")


def count(text: str) -> dict[str, int]:
    out = {name: len(enc.encode(text, disallowed_special=())) for name, enc in ENCODINGS.items()}
    return out | {"bytes": len(text.encode()), "units": units(text)}


def timing(value: Any, arm: str) -> dict[str, float]:
    seconds = []
    for _ in range(RUNS):
        began = time.perf_counter()
        render(value, arm)
        seconds.append(time.perf_counter() - began)
    seconds.sort()
    return {
        "median_ms": 1e3 * statistics.median(seconds),
        "p95_ms": 1e3 * seconds[int(0.95 * RUNS) - 1],
    }


def decodes(text: str, decode: Any) -> tuple[bool, Any]:
    try:
        return True, decode(text)
    except Exception as exc:  # noqa: BLE001 - recording whether the cut is detected
        return False, f"{type(exc).__name__}: {str(exc)[:200]}"


def sizes() -> None:
    cases = json.loads((DATA / "cases.json").read_text())
    adapter = TypeAdapter(SearchResult)
    rows = []
    for case in cases:
        if case["kind"] == "nav":
            continue  # same texts as its search20 case
        value = case["value"]
        row: dict[str, Any] = {"id": case["id"], "kind": case["kind"], "corpus": case["corpus"]}
        row["sizes"] = {arm: count(text) for arm, text in case["texts"].items()}
        if case["kind"] == "truncated":
            strict = decodes(case["texts"]["T"], toon_format.decode)
            lenient = decodes(case["texts"]["T"], lambda t: toon_format.decode(t, strict=False))
            compact = decodes(case["texts"]["C"], json.loads)
            row["decode_cut"] = {
                "T_strict": "decoded" if strict[0] else strict[1],
                "T_lenient": f"decoded {len(lenient[1]['entries'])} entries"
                if lenient[0] else lenient[1],
                "C": "decoded" if compact[0] else compact[1],
            }  # fmt: skip
        else:
            row["round_trip"] = {
                arm: decodes(case["texts"][arm], toon_format.decode)[1] == value
                for arm in ("T", "Ttab")
            }
            if case["kind"].endswith("bundle"):
                env = envelope(value)
                row["round_trip"]["ET"] = toon_format.decode(case["texts"]["ET"]) == env
            row["timing"] = {arm: timing(value, arm) for arm in ("C", "P", "T", "Ttab")}
        if "search" in case["kind"] or case["kind"] == "edge_empty":
            # Today's MCP text, rebuilt from the value through FastMCP's own serializer.
            mcp = default_serializer(adapter.validate_python(value))
            row["mcp_equals_C"] = mcp == case["texts"]["C"]
        rows.append(row)
        print(case["id"], file=sys.stderr)
    (RESULTS / "sizes.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")


def package() -> None:
    dist = importlib.metadata.distribution("toon-format")
    files = [f for f in dist.files or [] if f.parts[0] == "toon_format"]
    size = sum(Path(str(dist.locate_file(f))).stat().st_size for f in files)
    probe = "import time;t=time.perf_counter();import {};print(time.perf_counter()-t)"
    imports = {}
    for module in ("json", "toon_format"):
        runs = [
            float(
                subprocess.run(
                    [sys.executable, "-c", probe.format(module)],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
            )  # fmt: skip
            for _ in range(10)
        ]
        imports[module] = round(1e3 * min(runs), 2)
    report = {
        "version": dist.version,
        "requires": dist.requires,
        "license": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
        "requires_python": dist.metadata.get("Requires-Python"),
        "files": len(files),
        "installed_bytes": size,
        "import_ms_min_of_10": imports,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "package.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    {"conformance": conformance, "sizes": sizes, "package": package}[sys.argv[1]]()
