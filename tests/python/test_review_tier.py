import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_review_tier_classifies_paths() -> None:
    cases = [
        ("docs/notes.md", 0, ""),
        ("README.md", 0, ""),
        ("new/unknown/notes.md", 0, ""),
        (".github/workflows/ci.yml", 2, "docs/review/README.md"),
        ("src/oxide/new/AGENTS.md", 2, "docs/review/README.md"),
        ("docs/review/README.md", 2, "docs/review/README.md"),
        ("docs/superpowers/specs/intent.md", 2, "docs/review/README.md"),
        ("mise.toml", 2, "docs/review/README.md"),
        (
            "src/oxide/core/retrieval.py",
            2,
            "docs/review/core-and-retrieval.md,docs/review/README.md",
        ),
        (
            "src/oxide/core/languages/python.py",
            1,
            "docs/review/capture-and-language.md,docs/review/core-and-retrieval.md,"
            "docs/review/README.md",
        ),
        ("new/unknown/file.bin", 1, "docs/review/README.md"),
        (
            "src/oxide/storage/store.py",
            2,
            "docs/review/core-and-retrieval.md,docs/review/README.md,"
            "docs/review/storage-and-generations.md",
        ),
        (
            "src/oxide/api.py",
            2,
            "docs/review/api-surface.md,docs/review/core-and-retrieval.md,"
            "docs/review/README.md,docs/review/storage-and-generations.md",
        ),
        (
            "src/oxide/core/indexing.py",
            2,
            "docs/review/capture-and-language.md,docs/review/core-and-retrieval.md,"
            "docs/review/README.md",
        ),
        (
            "src/oxide/models.py",
            2,
            "docs/review/api-surface.md,docs/review/capture-and-language.md,docs/review/README.md",
        ),
        ("tests/python/test_capture.py", 1, "docs/review/README.md"),
        ("tests/python/conftest.py", 2, "docs/review/README.md"),
        (
            "src/oxide/capture.py",
            2,
            "docs/review/capture-and-language.md,docs/review/README.md,"
            "docs/review/storage-and-generations.md",
        ),
        (".claude/settings.json", 2, "docs/review/README.md"),
        ("src/oxide/cli.py", 2, "docs/review/api-surface.md,docs/review/README.md"),
        ("src/oxide/mcp_server.py", 2, "docs/review/api-surface.md,docs/review/README.md"),
        (
            "tests/python/contextbench.py",
            2,
            "docs/review/evidence-and-benchmarks.md,docs/review/README.md",
        ),
        (
            "tests/python/contextbench_ts.py",
            2,
            "docs/review/evidence-and-benchmarks.md,docs/review/README.md",
        ),
        (
            "docs/public-api.md",
            2,
            "docs/review/api-surface.md,docs/review/README.md",
        ),
    ]
    for path, tier, rubrics in cases:
        result = subprocess.run(
            [sys.executable, "scripts/review_tier.py", path],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.splitlines() == [f"tier={tier}", f"rubrics={rubrics}"]


def test_review_tier_uses_maximum_across_all_paths() -> None:
    cases = [
        (
            ["docs/notes.md", "src/oxide/core/languages/python.py"],
            "tier=1\nrubrics=docs/review/capture-and-language.md,"
            "docs/review/core-and-retrieval.md,docs/review/README.md\n",
        ),
        (
            ["docs/notes.md", "src/oxide/core/retrieval.py"],
            "tier=2\nrubrics=docs/review/core-and-retrieval.md,docs/review/README.md\n",
        ),
    ]
    for paths, expected in cases:
        result = subprocess.run(
            [sys.executable, "scripts/review_tier.py", *paths],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout == expected


def test_review_tier_parses_unusual_git_filenames(tmp_path: Path) -> None:
    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "--quiet")
    git("config", "user.name", "Tier Test")
    git("config", "user.email", "tier-test@example.invalid")
    path = tmp_path / "src" / "oxide" / "core" / "café.py"
    path.parent.mkdir(parents=True)
    path.write_text("value = 1\n", encoding="utf-8")
    git("add", "--all")
    git("commit", "--quiet", "-m", "base")
    base = git("rev-parse", "HEAD").stdout.strip()
    path.write_text("value = 2\n", encoding="utf-8")
    git("add", "--all")
    git("commit", "--quiet", "-m", "change")

    head = git("rev-parse", "HEAD").stdout.strip()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "review_tier.py"), "--base", base, "--head", head],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == [
        "tier=2",
        "rubrics=docs/review/core-and-retrieval.md,docs/review/README.md",
    ]
