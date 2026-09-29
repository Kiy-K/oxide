#!/usr/bin/env python3
"""Fresh held-out validation set for the axis-2 fix design -- deliberately
NOT the eval-agent/tasks fixtures used as the dev set. Uses
fixtures/py_repo/oxidepy/{auth,http_client,retry}.py, which the dev set
(review_relevance_labels.json) never touched, to check the two candidate
fixes (docs/evals/phase-4.2-typesafe/raw/fix_design.md) generalize rather
than being tuned to the 39 dev-set pairs.

H1 (genuine bug): AuthService.refresh_token always hits the same
"auth/refresh" endpoint regardless of session_id -- a real multi-tenant bug.
Nested inside AuthService, which has two unrelated sibling methods
(__init__, login), testing Fix 1 (innermost-seed attribution).

H2 (clean control): a docstring-only change to parse_headers, a top-level
function in a file that imports RetryPolicy/TooManyAttemptsError from
retry.py but never references either -- testing Fix 2 (reference-gated
imported-definition).

Must be run against BOTH the pre-fix and post-fix binaries to compare.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT_DIR = Path(__file__).resolve().parent
WORK = Path("/tmp/oxide-typesafe-heldout")


def sh(cmd, cwd=None, env=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd} failed:\n{r.stdout}\n{r.stderr}")
    return r


def git_commit(repo, msg):
    sh(["git", "add", "-A"], cwd=repo)
    sh(["git", "-c", "user.email=eval@local", "-c", "user.name=eval",
        "commit", "-q", "-m", msg], cwd=repo)
    return sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()


def index(oxide_bin, repo):
    sh([str(oxide_bin), "index", "--json"], cwd=repo, env={"OXIDE_EMBED_NATIVE": "hashed"})


def review(oxide_bin, repo, diff_range):
    r = sh([str(oxide_bin), "review", "--diff", diff_range, "--json"], cwd=repo,
           env={"OXIDE_EMBED_NATIVE": "hashed"})
    return json.loads(r.stdout)


def build_repo():
    repo = WORK / "py_repo"
    if repo.exists():
        shutil.rmtree(repo)
    shutil.copytree(ROOT / "fixtures/py_repo", repo, ignore=shutil.ignore_patterns(".oxide"))
    sh(["git", "init", "-q"], cwd=repo)
    base = git_commit(repo, "base py_repo fixture")

    auth_path = repo / "oxidepy/auth.py"
    src = auth_path.read_text()
    fixed_src = src.replace(
        '        fresh = self.client.fetch("auth/refresh").decode()\n',
        '        fresh = self.client.fetch(f"auth/refresh/{session_id}").decode()\n',
    )
    assert fixed_src != src, "H1 substitution did not match auth.py's current text"
    auth_path.write_text(fixed_src)
    h1_fixed = git_commit(repo, "fix: scope token refresh to the requesting session_id")

    http_path = repo / "oxidepy/http_client.py"
    src2 = http_path.read_text()
    cosmetic_src = src2.replace(
        'def parse_headers(raw_headers):\n'
        '    """Normalize urllib header tuples into a plain dict."""\n',
        'def parse_headers(raw_headers):\n'
        '    """Normalize urllib header tuples into a plain dict.\n\n'
        '    Keys are lowercased so callers can look them up case-insensitively.\n'
        '    """\n',
    )
    assert cosmetic_src != src2, "H2 substitution did not match http_client.py's current text"
    http_path.write_text(cosmetic_src)
    h2_cosmetic = git_commit(repo, "docs: expand parse_headers docstring")

    return {
        "repo": repo,
        "h1_diff": f"{base}..{h1_fixed}",
        "h2_diff": f"{h1_fixed}..{h2_cosmetic}",
    }


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    variant = sys.argv[1] if len(sys.argv) > 1 else "prefix"
    oxide_bin = {
        "prefix": ROOT / "target/release/oxide",
        "postfix": Path("/tmp/oxide-review-fix-experiment/target/release/oxide"),
    }[variant]
    assert oxide_bin.exists(), f"{oxide_bin} missing -- build it first"

    info = build_repo()
    index(oxide_bin, info["repo"])
    result = {
        "variant": variant,
        "h1_diff": info["h1_diff"],
        "h2_diff": info["h2_diff"],
        "h1_review": review(oxide_bin, info["repo"], info["h1_diff"]),
        "h2_review": review(oxide_bin, info["repo"], info["h2_diff"]),
    }
    out = OUT_DIR / f"heldout_output_{variant}.json"
    out.write_text(json.dumps(result, indent=2, default=str))
    print(f"Wrote {out}")
    for key in ("h1_review", "h2_review"):
        r = result[key]
        print(f"=== {key} ===")
        print("changed:", [s["qualified_name"] for s in r.get("changed_symbols", [])])
        print("related:", [(s["qualified_name"], round(s.get("score", 0), 3), s.get("reasons"))
                            for s in r.get("related", [])])


if __name__ == "__main__":
    main()
