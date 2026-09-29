#!/usr/bin/env python3
"""Builds two tiny git repos (from eval-agent's existing bug fixtures) each
with a genuine-bug-fix commit and a cosmetic/clean-control commit, indexes
them, and runs `oxide review --json` on both diffs. No OXIDE production code
is touched -- these are copies of existing eval-agent fixtures, not `src/`.

Never sends OXIDE's real source anywhere: this only touches the already-public
eval-agent fixture files, and the only network calls in this experiment go to
the TypeSafe API in a later script, carrying these same small fixture snippets
-- never OXIDE's own `src/`.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OXIDE_BIN = ROOT / "target/release/oxide"
OUT_DIR = Path(__file__).resolve().parent
WORK = Path("/tmp/oxide-typesafe-axis2")


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


def index(repo):
    r = sh([str(OXIDE_BIN), "index", "--json"], cwd=repo, env={"OXIDE_EMBED_NATIVE": "hashed"})
    return json.loads(r.stdout)


def review(repo, diff_range):
    r = sh([str(OXIDE_BIN), "review", "--diff", diff_range, "--json"], cwd=repo,
           env={"OXIDE_EMBED_NATIVE": "hashed"})
    return json.loads(r.stdout)


def build_py_retry():
    repo = WORK / "py_retry"
    if repo.exists():
        shutil.rmtree(repo)
    shutil.copytree(ROOT / "eval-agent/tasks/py_bug_retry", repo)
    sh(["git", "init", "-q"], cwd=repo)
    buggy = git_commit(repo, "buggy retry backoff")

    retry_path = repo / "app/retry.py"
    src = retry_path.read_text()
    fixed_src = src.replace(
        '    def backoff_ms(self, attempt):\n'
        '        # BUG: delay shrinks instead of growing; callers starve the server.\n'
        '        return self.base_delay_ms // (attempt + 1)\n',
        '    def backoff_ms(self, attempt):\n'
        '        return self.base_delay_ms * (2 ** attempt)\n',
    )
    assert fixed_src != src, "fix substitution did not match retry.py's current text"
    retry_path.write_text(fixed_src)
    fixed = git_commit(repo, "fix: grow retry backoff exponentially instead of shrinking it")

    http_path = repo / "app/http.py"
    src2 = http_path.read_text()
    cosmetic_src = src2.replace(
        '    def request_json(self, path):\n'
        '        body = self._fetch(path)\n'
        '        return json.loads(body)\n',
        '    def request_json(self, path):\n'
        '        """Fetch `path` and parse the response body as JSON."""\n'
        '        body = self._fetch(path)\n'
        '        return json.loads(body)\n',
    )
    assert cosmetic_src != src2, "cosmetic substitution did not match http.py's current text"
    http_path.write_text(cosmetic_src)
    cosmetic = git_commit(repo, "docs: document HttpClient.request_json")

    index(repo)
    return {
        "repo": repo,
        "bug_diff": f"{buggy}..{fixed}",
        "clean_diff": f"{fixed}..{cosmetic}",
    }


def build_ts_store():
    repo = WORK / "ts_store"
    if repo.exists():
        shutil.rmtree(repo)
    shutil.copytree(ROOT / "eval-agent/tasks/ts_bug_store", repo)
    sh(["git", "init", "-q"], cwd=repo)
    buggy = git_commit(repo, "buggy versioned store")

    store_path = repo / "src/versioned_store.ts"
    src = store_path.read_text()
    fixed_src = src.replace(
        "  set(key: string, value: T): number {\n"
        "    // BUG: version never advances; optimistic-concurrency checks break.\n"
        "    const previous = this.entries.get(key);\n"
        "    const version = previous?.version ?? this.initialVersion;\n"
        "    this.entries.set(key, { value, version });\n"
        "    return version;\n"
        "  }\n",
        "  set(key: string, value: T): number {\n"
        "    const previous = this.entries.get(key);\n"
        "    const version = previous ? previous.version + 1 : this.initialVersion;\n"
        "    this.entries.set(key, { value, version });\n"
        "    return version;\n"
        "  }\n",
    )
    assert fixed_src != src, "fix substitution did not match versioned_store.ts's current text"
    store_path.write_text(fixed_src)
    fixed = git_commit(repo, "fix: advance version on every set() for an existing key")

    audit_path = repo / "src/audit.ts"
    src2 = audit_path.read_text()
    cosmetic_src = src2.replace(
        "  record(eventId: string, line: string): number {\n",
        "  /** Append `line` to the event's audit trail and return its new version. */\n"
        "  record(eventId: string, line: string): number {\n",
    )
    assert cosmetic_src != src2, "cosmetic substitution did not match audit.ts's current text"
    audit_path.write_text(cosmetic_src)
    cosmetic = git_commit(repo, "docs: document AuditLog.record")

    index(repo)
    return {
        "repo": repo,
        "bug_diff": f"{buggy}..{fixed}",
        "clean_diff": f"{fixed}..{cosmetic}",
    }


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    assert OXIDE_BIN.exists(), "build the release binary first: cargo build --release -j 2"

    results = {}
    for name, builder in [("py_retry", build_py_retry), ("ts_store", build_ts_store)]:
        info = builder()
        results[name] = {
            "repo": str(info["repo"]),
            "bug_diff": info["bug_diff"],
            "clean_diff": info["clean_diff"],
            "bug_review": review(info["repo"], info["bug_diff"]),
            "clean_review": review(info["repo"], info["clean_diff"]),
        }
        print(f"=== {name} bug diff ({info['bug_diff']}) ===")
        print(json.dumps(results[name]["bug_review"], indent=2)[:2000])
        print(f"=== {name} clean diff ({info['clean_diff']}) ===")
        print(json.dumps(results[name]["clean_review"], indent=2)[:2000])

    out = OUT_DIR / "review_fixture_output.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
