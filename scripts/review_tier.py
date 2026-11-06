"""Review tier of a change (docs/agents/workflow.md § Review tiers).

    python scripts/review_tier.py                  # working tree + untracked vs HEAD
    python scripts/review_tier.py --base origin/main   # BASE...HEAD (CI)
    python scripts/review_tier.py PATH...          # explicit paths

Prints `tier=N` and `rubrics=a,b` (docs/review/<name>.md), ready for $GITHUB_OUTPUT.
"""

from __future__ import annotations

import argparse
import os
import subprocess

# Tier 2 paths/prefixes cover load-bearing contracts, policies and trust boundaries.
TIER2 = (
    "src/oxide/api.py",
    "src/oxide/models.py",
    "src/oxide/__init__.py",
    "src/oxide/storage/",
    "src/oxide/capture.py",
    "src/oxide/core/",
    "fixtures/eval/",
    "tests/python/test_baseline.py",
    "tests/python/test_contextbench",
    "pyproject.toml",
    "uv.lock",
    "mise.toml",
    ".github/",
    ".claude/",
    ".codex/",
    ".pi/",
    ".entire/",
    ".agents/",
    ".opencode/",
    ".mcp.json",
    "AGENTS.md",
    "CLAUDE.md",
    "REVIEW.md",
    "docs/agents/workflow.md",
    "docs/public-api.md",
    "docs/superpowers/specs/",
    "docs/review/",
    "docs/spec/",
    "docs/decisions.md",
    "docs/adr/",
)

# Language extractors are routine tier-1 work despite living under core/.
TIER2_EXCEPT = ("src/oxide/core/languages/",)
# Nested agent instructions are policy paths too.
TIER2_SUFFIX = ("AGENTS.md", "CLAUDE.md")
TIER2_EXACT = (
    "tests/python/conftest.py",
    "tests/python/contextbench.py",
    "tests/python/contextbench_ts.py",
    "src/oxide/cli.py",
    "src/oxide/mcp_server.py",
    "scripts/review_tier.py",
)
TIER0 = ("docs/",)
TIER0_SUFFIX = (".md",)
RUBRICS = (
    (
        "src/oxide/storage/",
        (
            "docs/review/core-and-retrieval.md",
            "docs/review/storage-and-generations.md",
        ),
    ),
    (
        "src/oxide/capture.py",
        (
            "docs/review/capture-and-language.md",
            "docs/review/storage-and-generations.md",
        ),
    ),
    ("src/oxide/core/languages/", ("docs/review/capture-and-language.md",)),
    ("src/oxide/core/indexing.py", ("docs/review/capture-and-language.md",)),
    ("src/oxide/core/", ("docs/review/core-and-retrieval.md",)),
    (
        "src/oxide/api.py",
        (
            "docs/review/api-surface.md",
            "docs/review/core-and-retrieval.md",
            "docs/review/storage-and-generations.md",
        ),
    ),
    ("src/oxide/__init__.py", ("docs/review/api-surface.md",)),
    (
        "src/oxide/models.py",
        (
            "docs/review/api-surface.md",
            "docs/review/capture-and-language.md",
        ),
    ),
    ("src/oxide/cli.py", ("docs/review/api-surface.md",)),
    ("src/oxide/mcp_server.py", ("docs/review/api-surface.md",)),
    ("docs/public-api.md", ("docs/review/api-surface.md",)),
    ("fixtures/eval/", ("docs/review/evidence-and-benchmarks.md",)),
    (
        "tests/python/test_baseline.py",
        ("docs/review/evidence-and-benchmarks.md",),
    ),
    (
        "tests/python/test_contextbench",
        ("docs/review/evidence-and-benchmarks.md",),
    ),
    ("tests/python/contextbench.py", ("docs/review/evidence-and-benchmarks.md",)),
    ("tests/python/contextbench_ts.py", ("docs/review/evidence-and-benchmarks.md",)),
)


def _tier(path: str) -> int:
    if (
        path in TIER2_EXACT
        or path.endswith(TIER2_SUFFIX)
        or (path.startswith(TIER2) and not path.startswith(TIER2_EXCEPT))
    ):
        return 2
    if path.startswith(TIER0) or path.endswith(TIER0_SUFFIX):
        return 0
    return 1


def classify(paths: list[str]) -> tuple[int, list[str]]:
    """Max tier over `paths`, and reviewer rubric filenames."""
    tier = max(map(_tier, paths), default=0)
    rubrics: dict[str, None] = {"docs/review/README.md": None} if tier else {}
    if tier:
        for path in paths:
            for prefix, names in RUBRICS:
                if path.startswith(prefix):
                    rubrics.update(dict.fromkeys(names))
    return tier, sorted(rubrics, key=str.casefold)


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], check=True, capture_output=True).stdout
    return [os.fsdecode(path) for path in out.split(b"\0") if path]


def changed_paths(base: str | None, head: str | None) -> list[str]:
    if base:
        return _git("diff", "--name-only", "--no-renames", "-z", f"{base}...{head or 'HEAD'}")
    return _git("diff", "--name-only", "--no-renames", "-z", "HEAD") + _git(
        "ls-files", "--others", "--exclude-standard", "-z"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify changed paths by required review tier")
    parser.add_argument("--base", help="compare BASE...HEAD instead of the working tree")
    parser.add_argument("--head", help="end commit when comparing BASE...HEAD")
    parser.add_argument("paths", nargs="*", help="classify these paths instead of a diff")
    args = parser.parse_args()
    if args.head and not args.base:
        parser.error("--head requires --base")
    tier, rubrics = classify(args.paths or changed_paths(args.base, args.head))
    print(f"tier={tier}")
    print(f"rubrics={','.join(rubrics)}")


if __name__ == "__main__":
    main()
