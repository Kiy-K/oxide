"""Study C/D: wheel coverage on PyPI for OXIDE's supported targets (metadata only).

Supported targets (docs/platforms.md): CPython 3.12-3.14 on Linux x86_64 (manylinux,
glibc >= 2.28), macOS x86_64, macOS arm64 and Windows x86_64. For ast-grep-py 0.51.0
and the tree-sitter grammar wheels OXIDE would need per language, report which targets
lack a wheel (an abi3 wheel covers every later CPython) and the Linux wheel size.

    pypi_matrix.py OUT_JSON
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request

PACKAGES = {
    "ast-grep-py": "0.51.0",
    "tree-sitter": "0.26.0",
    "tree-sitter-python": "0.25.0",
    "tree-sitter-typescript": "0.23.2",
    "tree-sitter-javascript": None,  # None: the latest release
    "tree-sitter-java": None,
    "tree-sitter-go": None,
    "tree-sitter-c": None,
    "tree-sitter-cpp": None,
}
PYTHONS = (12, 13, 14)
PLATFORMS = {
    "linux-x86_64": re.compile(r"manylinux.*x86_64"),
    "macos-x86_64": re.compile(r"macosx.*(x86_64|universal2)"),
    "macos-arm64": re.compile(r"macosx.*(arm64|universal2)"),
    "windows-x86_64": re.compile(r"win_amd64"),
}


def covers(filename: str, minor: int, platform: str) -> bool:
    py, abi, plat = filename[:-4].split("-")[-3:]
    if not PLATFORMS[platform].search(plat):
        return False
    tags = py.split(".")
    if abi == "abi3":
        return any(t.startswith("cp3") and int(t[3:]) <= minor for t in tags)
    return f"cp3{minor}" in tags


def main() -> None:
    report = {}
    for package, pinned in PACKAGES.items():
        url = (
            f"https://pypi.org/pypi/{package}/{pinned}/json"
            if pinned
            else (f"https://pypi.org/pypi/{package}/json")
        )
        with urllib.request.urlopen(url) as response:
            data = json.load(response)
        wheels = [f for f in data["urls"] if f["filename"].endswith(".whl")]
        missing = [
            f"{platform}/cp3{minor}"
            for platform in PLATFORMS
            for minor in PYTHONS
            if not any(covers(w["filename"], minor, platform) for w in wheels)
        ]
        linux = [w["size"] for w in wheels if covers(w["filename"], 12, "linux-x86_64")]
        report[package] = {
            "version": data["info"]["version"],
            "license": data["info"].get("license_expression") or data["info"].get("license"),
            "wheels": len(wheels),
            "missing_targets": missing,
            "linux_x86_64_cp312_wheel_bytes": min(linux) if linux else None,
            "sdist": any(f["packagetype"] == "sdist" for f in data["urls"]),
        }
        print(package, report[package], file=sys.stderr)
    with open(sys.argv[1], "w") as out:
        json.dump(report, out, indent=1)
        out.write("\n")


if __name__ == "__main__":
    main()
