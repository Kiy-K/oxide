#!/usr/bin/env python3
"""Reproducible SQLite corpus-load baseline (roadmap #9 item 4, issue #10).

Drives the existing tools — `examples/retrieval_profile.rs --stage` (isolated
per-stage time/allocation samples), `scripts/mcp_bench.py --json` (MCP
first-call and steady-state), and the release `oxide` binary itself
(one-shot CLI latency + peak RSS via `os.wait4`, index/edit costs, db/WAL
sizes) — over pinned corpora, and writes machine-readable raw samples plus
a manifest under `docs/retrieval-profile/corpus-load-baseline/raw/`.
`report` folds the raw files into one summary table (`summary.md`).

    scripts/corpus_load_baseline.py all   --work <dir>      # everything below
    scripts/corpus_load_baseline.py setup --work <dir>      # clone + index corpora
    scripts/corpus_load_baseline.py index-costs|stages|cli|mcp|parity|report ...

`--work` holds the corpus clones (`src/`), the indexed copies every read
measurement runs against (`idx/`), throwaway copies for write-cost runs
(`tmp/`) and the two binaries the parity check compares (`bin/`). Every
measurement uses the offline hashed embedder (`OXIDE_EMBED_NATIVE=hashed`)
so numbers are retrieval, not inference; `cli` adds a small native-default
check on `requests` alone, reported separately.

Measurement discipline (see the report for the rationale):
- every stage/surface sample comes from its own process, run in two
  interleaved batches (A, B, A, B, …) of `--reps` (default 5) each;
  repetition 0 inside a process is *process-cold*, later repetitions are
  *warmed* — nothing here is OS-page-cache-cold, and the harness never
  claims it is;
- overlapping stage totals are reported side by side, never summed;
- the `payload` diagnostic runs outside every timed window.
"""
import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DEFAULT = ROOT / "docs/retrieval-profile/corpus-load-baseline/raw"

# Pinned corpora: (name, git url, tag). Symbol counts are *measured* and
# recorded in the manifest, never assumed from the earlier profile.
CORPORA = [
    ("requests", "https://github.com/psf/requests.git", "v2.34.2"),
    ("pytest", "https://github.com/pytest-dev/pytest.git", "9.1.1"),
    ("pylint", "https://github.com/pylint-dev/pylint.git", "v4.0.8"),
]
FIXTURES = ["py_repo", "ts_repo"]

# One query per corpus for the stage/CLI/MCP measurements, chosen so that
# `search` (expansion on) actually reaches structural expansion — `stages`
# warns when `search_expand` reports `expanded_hits=0` and the report
# records the count. Parity uses the wider list below.
QUERIES = {
    "requests": "prepare request headers cookies",
    "pytest": "fixture finalizer teardown",
    "pylint": "checker visit node message",
    "py_repo": "retry policy",
    "ts_repo": "retry policy",
}
PARITY_QUERIES = [
    "retry policy",
    "http client fetch",
    "parse config file",
    "test fixture setup",
    "cache invalidate",
    "error handling exception",
    "session cookies headers",
    "walk directory files",
]
LITERAL_PATTERNS = [
    "self",
    "def ",
    "import os",
    "TODO",
    "raise ValueError",
    "class ",
    "return None",
    "\\r",
    "zzz_absent_pattern_zzz",
    "assert ",
    "@property",
]
STAGES = [
    "all_symbols",
    "all_symbols_rows_floor",
    "all_symbols_rows_floor_unordered",
    "all_symbol_relations",
    "snapshot_load",
    "relation_index",
    "callers_index",
    "hydrate",
    "search_noexpand",
    "search_expand",
    "context",
    "context_cached",
]
SEARCH_SURFACES = {
    "search-lexical": ["search", "-m", "lexical"],
    "search-semantic": ["search", "-m", "semantic"],
    "search-hybrid": ["search"],
    "search-no-expand": ["search", "--no-expand"],
    "search-quality": ["search", "--profile", "quality"],
    "search-blast": ["search", "--blast-radius"],
    "context-fast": ["query", "--profile", "fast"],
    "context-balanced": ["query"],
    "context-quality": ["query", "--profile", "quality"],
    "context-git": ["query", "--git"],
    "context-blast": ["query", "--blast-radius"],
}
CLI_SURFACES = {
    "search": ["search"],
    "search-no-expand": ["search", "--no-expand"],
    "context": ["query"],
}
# Opt-in extra CLI surfaces (`--cli-surfaces`): git-aware requests need a
# working-tree diff, which the pinned clean clones do not have.
GIT_CLI_SURFACES = {
    "context-git": ["query", "--git"],
    "review": ["review", "--diff", ""],
}


def hashed_env(native=False):
    """Environment for every child: provider selection is forced to the
    hashed profile (or the native default for the one native check) and
    every other provider tier is removed — `$OXIDE_EMBED_URL/MODEL`, the
    `$OXIDE_EMBED_PROVIDER` + `$OXIDE_*_API_KEY` remote opt-in, and
    `oxide setup`'s saved `config.toml` (pointed at an empty
    `$XDG_CONFIG_HOME`), since `open_embedder` resolves a configured
    remote provider *above* `$OXIDE_EMBED_NATIVE`. The index's own
    `meta.embedder` is asserted afterwards (`assert_embedder`), so a
    mislabeled provider cannot reach the manifest."""
    env = {k: v for k, v in os.environ.items() if not (k.startswith("OXIDE_") and k.endswith("_API_KEY"))}
    for k in ("OXIDE_EMBED_URL", "OXIDE_EMBED_MODEL", "OXIDE_EMBED_PROVIDER", "OXIDE_RETRIEVAL_MODE"):
        env.pop(k, None)
    env["XDG_CONFIG_HOME"] = str(EMPTY_CONFIG_HOME)
    env["OXIDE_EMBED_SESSIONS"] = "1"
    if native:
        env.pop("OXIDE_EMBED_NATIVE", None)
    else:
        env["OXIDE_EMBED_NATIVE"] = "hashed"
    return env


import tempfile

# A fresh, empty directory per invocation: nothing `oxide setup` may have
# saved under the real `$XDG_CONFIG_HOME` can be found here.
EMPTY_CONFIG_HOME = Path(tempfile.mkdtemp(prefix="oxide-baseline-empty-config-"))
HASHED = "hashed-bow-256"
NATIVE_PREFIX = "native:"


def assert_embedder(p, repo, expected):
    """The provider identity the index was actually written with."""
    payload = json.loads(
        sh([p.profile, str(repo), "x", "--stage", "payload", "--json"], env=hashed_env()).stdout
    )
    got = payload["meta"].get("embedder") or ""
    ok = got.startswith(expected) if expected.endswith(":") else got == expected
    if not ok:
        raise SystemExit(f"{repo}: index embedder is {got!r}, expected {expected!r}")
    return payload


def sh(cmd, cwd=None, env=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"command failed ({r.returncode}): {' '.join(map(str, cmd))}\n{r.stderr}")
    return r


def timed_child(cmd, env, cwd=None, stdout=subprocess.DEVNULL):
    """Wall-clock ms and peak RSS (KB, Linux `ru_maxrss`) of one child
    process, attributed to *that* child via `os.wait4` — `RUSAGE_CHILDREN`
    would only give the running max over every child so far."""
    t = time.perf_counter()
    p = subprocess.Popen(cmd, env=env, cwd=cwd, stdout=stdout, stderr=subprocess.PIPE)
    _, status, ru = os.wait4(p.pid, 0)
    ms = (time.perf_counter() - t) * 1000
    err = p.stderr.read().decode(errors="replace")
    code = os.waitstatus_to_exitcode(status)
    if code != 0:
        raise SystemExit(f"child failed ({code}): {' '.join(map(str, cmd))}\n{err}")
    return ms, ru.ru_maxrss


def median(xs):
    return statistics.median(xs) if xs else None


def spread(xs):
    return (min(xs), max(xs)) if xs else (None, None)


RUN_ID = time.strftime("%Y%m%dT%H%M%S")


def write_jsonl(path, rows, replace_where=None):
    """Writes one measurement step's rows. The file is *replaced*, never
    appended to, so a rerun of the documented sequence cannot mix its
    samples with the checked-in ones; `replace_where(row)` keeps existing
    rows it returns False for (a `--stages` subset rerun replaces only
    those stages' rows). Every row carries this invocation's `run_id`;
    `report` prints each file's run ids next to the manifest's, so a mix
    is visible in `summary.md` rather than silent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = [r for r in read_jsonl(path) if replace_where is not None and not replace_where(r)]
    for r in rows:
        r.setdefault("run_id", RUN_ID)
    with open(path, "w") as f:
        for r in kept + rows:
            f.write(json.dumps(r) + "\n")


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in open(path) if l.strip()]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Paths:
    def __init__(self, args):
        self.work = Path(args.work).resolve()
        self.out = Path(args.out).resolve()
        self.src = self.work / "src"
        self.idx = self.work / "idx"
        self.tmp = self.work / "tmp"
        self.oxide = Path(args.oxide).resolve()
        self.profile = Path(args.profile).resolve()
        # (tag, oxide, profile) per binary under test. With a challenger,
        # stages/cli/mcp interleave baseline and challenger inside every
        # batch, so both share one same-window null.
        # A challenger needs both builds: a tag must never label samples
        # that actually ran the baseline binary.
        self.variants = [("base", self.oxide, self.profile)]
        if bool(args.challenger_oxide) != bool(args.challenger_profile):
            raise SystemExit("--challenger-oxide and --challenger-profile go together")
        if args.challenger_oxide:
            self.variants.append((
                "challenger",
                Path(args.challenger_oxide).resolve(),
                Path(args.challenger_profile).resolve(),
            ))
        # An A/A validity control: the baseline again, as its own variant,
        # inside the same (shuffled) schedule as the challenger.
        if getattr(args, "aa_control", False):
            self.variants.append(("base_aa", self.oxide, self.profile))
        self.corpora = [c for c, _, _ in CORPORA] + FIXTURES
        if args.corpora:
            self.corpora = args.corpora.split(",")

    def ordered(self, i):
        """`variants`, reversed on odd repetitions, so neither binary always
        runs first (and on a machine the other one just warmed)."""
        return self.variants if i % 2 == 0 else self.variants[::-1]


# ---------------------------------------------------------------- setup ----


def cmd_setup(p, args):
    p.src.mkdir(parents=True, exist_ok=True)
    for name, url, tag in CORPORA:
        if name in p.corpora and not (p.src / name).exists():
            print(f"clone {name}@{tag}")
            sh(["git", "clone", "-q", "--depth", "1", "--branch", tag, url, str(p.src / name)])
    for name in FIXTURES:
        if name not in p.corpora:
            continue
        dst = p.src / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(ROOT / "fixtures" / name, dst)
        shutil.rmtree(dst / ".oxide", ignore_errors=True)
    # The indexed copies every read-side measurement uses: fresh index,
    # hashed embedder. `oxide index` closes its writer before exiting, so
    # the WAL is checkpointed and the copy is a settled index.
    p.idx.mkdir(parents=True, exist_ok=True)
    for name in p.corpora:
        dst = p.idx / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(p.src / name, dst, symlinks=True)
        print(f"index {name}")
        sh([p.oxide, "index", "--json", str(dst)], env=hashed_env())
        assert_embedder(p, dst, HASHED)
    cmd_manifest(p, args)


def cmd_manifest(p, args):
    """Provenance for the indexed copies as they are now: corpus commits,
    index metadata, payload shape, toolchain and machine. Separate from
    `setup` so it can be refreshed without re-indexing (which would change
    the very index the raw samples were taken against)."""
    manifest = {"corpora": {}}
    for name in p.corpora:
        pinned = next(((u, t) for n, u, t in CORPORA if n == name), None)
        src = p.src / name
        if pinned:
            entry = {
                "url": pinned[0],
                "tag": pinned[1],
                "commit": sh(["git", "rev-parse", "HEAD"], cwd=src).stdout.strip(),
            }
        else:
            entry = {"source": f"fixtures/{name}", "commit": oxide_commit()}
        dst = p.idx / name
        payload = assert_embedder(p, dst, HASHED)
        order = json.loads(
            sh([p.profile, str(dst), "x", "--stage", "order_digest", "--json"], env=hashed_env()).stdout
        )
        entry.update(
            {
                "status": status_or_error(p, dst),
                "payload": payload,
                "order_digest": order,
                "index_db_bytes": (dst / ".oxide/index.db").stat().st_size,
            }
        )
        manifest["corpora"][name] = entry
    native = p.idx / "requests-native"
    if native.exists():
        # The native check's index (built by `cli`), with its identity
        # asserted to be a native-profile space.
        manifest["native_check_index"] = {
            "corpus": "requests",
            "payload": assert_embedder(p, native, NATIVE_PREFIX),
            "index_db_bytes": (native / ".oxide/index.db").stat().st_size,
        }
    manifest.update(environment(p))
    manifest["run_id"] = RUN_ID
    p.out.mkdir(parents=True, exist_ok=True)
    (p.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {p.out / 'manifest.json'}")


def status_or_error(p, repo):
    """`oxide status --json -v`, or its error object: `status` re-hashes
    every scanned file through `read_to_string` and fails on a repo that
    contains one non-UTF-8 source file (pylint's
    `tests/functional/i/implicit/implicit_str_concat_latin1.py`), which
    `oxide index` itself tolerates. A pre-existing `status` limitation, not
    something this harness works around beyond recording it; counts come
    from the `payload` diagnostic instead."""
    r = sh([p.oxide, "status", "--json", "-v", str(repo)], env=hashed_env(), check=False)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"error": r.stdout.strip() or r.stderr.strip()}


def oxide_commit():
    return sh(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip()


def environment(p):
    dirty = sh(["git", "status", "--porcelain", "--", "src", "Cargo.toml", "Cargo.lock"], cwd=ROOT).stdout
    cpu = ""
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    mem_kb = None
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal"):
                mem_kb = int(line.split()[1])
                break
    except OSError:
        pass
    return {
        "oxide_commit": oxide_commit(),
        "oxide_src_dirty": dirty.strip().splitlines(),
        "oxide_version": sh([p.oxide, "--version"]).stdout.strip(),
        "oxide_binary_sha256": sha256(p.oxide),
        "profile_binary_sha256": sha256(p.profile),
        "challenger_binaries_sha256": {
            "oxide": sha256(p.variants[1][1]),
            "profile": sha256(p.variants[1][2]),
        } if len(p.variants) > 1 else None,
        "rustc": sh(["rustc", "-V"], cwd=ROOT).stdout.strip(),
        "cargo": sh(["cargo", "-V"], cwd=ROOT).stdout.strip(),
        "rusqlite": cargo_lock_version("rusqlite"),
        "libsqlite3_sys": cargo_lock_version("libsqlite3-sys"),
        "platform": platform.platform(),
        "kernel": platform.release(),
        "cpu": cpu,
        "cpu_count": os.cpu_count(),
        "mem_total_kb": mem_kb,
        "python": platform.python_version(),
        "embedder": f"{HASHED} (OXIDE_EMBED_NATIVE=hashed, remote tiers removed, XDG_CONFIG_HOME empty) unless a row says native; asserted against every index's meta.embedder",
        "embed_sessions": "OXIDE_EMBED_SESSIONS=1 in hashed_env for every child",
        "retrieval_options": {
            "mode": "balanced (default; OXIDE_RETRIEVAL_MODE unset)",
            "search_limit": 10,
            "context_budget_tokens": 4096,
            "hydrate_candidates": 400,
            "note": "frozen: RRF K=60, lexical/vector 0.6/0.4, 200 candidates per channel — read from src/config.rs via src/retrieval/engine.rs, not tuned here",
        },
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def cargo_lock_version(crate):
    lock = (ROOT / "Cargo.lock").read_text().split("[[package]]")
    for block in lock:
        if f'name = "{crate}"' in block:
            for line in block.splitlines():
                if line.startswith("version = "):
                    return line.split('"')[1]
    return None


# ---------------------------------------------------------- index-costs ----


def wal_watch(db, stop, peak):
    wal = Path(str(db) + "-wal")
    while not stop.is_set():
        try:
            peak[0] = max(peak[0], wal.stat().st_size)
        except OSError:
            pass
        time.sleep(0.01)


def index_run(p, repo, label, name, batch, rep):
    db = repo / ".oxide/index.db"
    stop, peak = threading.Event(), [0]
    t = threading.Thread(target=wal_watch, args=(db, stop, peak), daemon=True)
    t.start()
    out = p.tmp / "index.json"
    with open(out, "w") as f:
        ms, rss = timed_child([p.oxide, "index", "--json", str(repo)], hashed_env(), stdout=f)
    stop.set()
    t.join()
    result = json.loads(out.read_text())
    wal = Path(str(db) + "-wal")
    return {
        "kind": "index",
        "corpus": name,
        "op": label,
        "batch": batch,
        "rep": rep,
        "ms": ms,
        "rss_kb": rss,
        "db_bytes": db.stat().st_size,
        "wal_peak_bytes": peak[0],
        "wal_final_bytes": wal.stat().st_size if wal.exists() else 0,
        "result": result,
    }


def edit_target(repo):
    """Largest indexable Python/TypeScript file — the same rule
    `scripts/perf.sh` uses, so the edit is a real new declaration (a
    trailing comment changes no symbol span and re-embeds nothing)."""
    exts = {
        ".py": "\ndef bench_edit_probe():\n    return 1\n",
        ".ts": "\nexport function benchEditProbe(): number {\n  return 1;\n}\n",
    }
    cands = [
        f
        for f in repo.rglob("*")
        if f.is_file() and f.suffix in exts and ".oxide" not in f.parts and ".git" not in f.parts
    ]
    target = max(cands, key=lambda f: f.stat().st_size)
    return target, exts[target.suffix]


def cmd_index_costs(p, args):
    p.tmp.mkdir(parents=True, exist_ok=True)
    rows = []
    # A,B,A,B,…: the two batches interleave at the finest grain (one
    # sample of each per corpus per turn) so drift over the run lands in
    # both equally.
    for rep in range(args.reps):
        for batch in range(2):
            for name in p.corpora:
                repo = p.tmp / name
                if repo.exists():
                    shutil.rmtree(repo)
                shutil.copytree(p.src / name, repo, symlinks=True)
                shutil.rmtree(repo / ".oxide", ignore_errors=True)
                rows.append(index_run(p, repo, "cold_index", name, batch, rep))
                rows.append(index_run(p, repo, "nochange_reindex", name, batch, rep))
                target, snippet = edit_target(repo)
                original = target.read_text()
                target.write_text(original + snippet)
                r = index_run(p, repo, "single_file_edit", name, batch, rep)
                r["edited_file"] = str(target.relative_to(repo))
                rows.append(r)
                target.write_text(original)
                print(
                    f"index-costs {name} b{batch} r{rep}: cold {rows[-3]['ms']:.0f} ms "
                    f"(wal peak {rows[-3]['wal_peak_bytes'] >> 10} KB), nochange {rows[-2]['ms']:.0f} ms, "
                    f"edit {rows[-1]['ms']:.0f} ms"
                )
    write_jsonl(p.out / "index_costs.jsonl", rows)


# --------------------------------------------------------------- stages ----


def cmd_stages(p, args):
    rows = []
    stages = args.stages.split(",") if args.stages else STAGES
    for rep in range(args.reps):
        for batch in range(2):
            for name in p.corpora:
                repo = p.idx / name
                for stage in stages:
                  for tag, _, profile in p.ordered(rep):
                    env = dict(hashed_env(), PROFILE_REPEAT=str(args.in_process))
                    r = sh([profile, str(repo), QUERIES[name], "--stage", stage, "--json"], env=env)
                    for line in r.stdout.splitlines():
                        d = json.loads(line)
                        d.update({"kind": "stage", "corpus": name, "batch": batch, "process": rep, "binary": tag})
                        d["cold"] = d["rep"] == 0
                        rows.append(d)
                        if stage == "search_expand" and d["rep"] == 0 and "expanded_hits=0" in d["note"]:
                            print(f"WARNING: {name}: search_expand did not expand ({d['note']})")
            print(f"stages rep {rep} batch {batch} done")
    write_jsonl(p.out / "stages.jsonl", rows, replace_where=lambda r: r["stage"] in stages)


# ------------------------------------------------------------------ cli ----


def cli_sample(p, repo, name, surface, argv, batch, rep, native=False, binary=None):
    env = hashed_env(native=native)
    out = p.tmp / "cli.json"
    p.tmp.mkdir(parents=True, exist_ok=True)
    query = [] if argv[0] == "review" else [QUERIES[name]]
    with open(out, "w") as f:
        ms, rss = timed_child(
            [binary or p.oxide, *argv, "--json", "--path", str(repo), *query], env, stdout=f
        )
    doc = json.loads(out.read_text())
    expanded = None
    if isinstance(doc, list):
        expanded = sum(1 for h in doc if any("←" in r for r in h.get("reasons", [])))
    return {
        "kind": "cli",
        "corpus": name,
        "surface": surface,
        "embedder": "native-default" if native else "hashed",
        "batch": batch,
        "rep": rep,
        "ms": ms,
        "rss_kb": rss,
        "expanded_hits": expanded,
        "items": len(doc) if isinstance(doc, list) else len(doc.get("items", [])),
    }


def cmd_cli(p, args):
    rows = []
    surfaces = dict(CLI_SURFACES)
    if args.git_surfaces:
        surfaces.update(GIT_CLI_SURFACES)
        # Timing git-aware requests on a clean tree measures empty work.
        for name in p.corpora:
            diff = sh(["git", "diff", "HEAD", "--quiet"], cwd=p.idx / name, check=False)
            if diff.returncode != 1:
                raise SystemExit(f"--git-surfaces: {name} has no worktree-vs-HEAD diff")
    for rep in range(args.reps):
        for batch in range(2):
            for name in p.corpora:
                for surface, argv in surfaces.items():
                    for tag, oxide, _ in p.ordered(rep):
                        row = cli_sample(p, p.idx / name, name, surface, argv, batch, rep, binary=oxide)
                        row["binary"] = tag
                        rows.append(row)
            print(f"cli rep {rep} batch {batch} done")
    write_jsonl(p.out / "cli.jsonl", rows)
    # Native-default check: `requests` alone, its own natively indexed copy.
    # The model load is per process and reported as part of the latency —
    # this is the "small separate check", never compared to hashed rows.
    if not args.skip_native and "requests" in p.corpora:
        repo = p.idx / "requests-native"
        if not repo.exists():
            shutil.copytree(p.src / "requests", repo, symlinks=True)
            sh([p.oxide, "index", "--json", str(repo)], env=hashed_env(native=True))
        # The native check's index must really be a native-profile space
        # (a `--no-default-features` build would silently have indexed it
        # hashed); the exact name is recorded on every row below.
        native_payload = assert_embedder(p, repo, NATIVE_PREFIX)
        native_rows = []
        for rep in range(args.reps):
            for batch in range(2):
                for surface, argv in CLI_SURFACES.items():
                    native_rows.append(
                        cli_sample(p, repo, "requests", surface, argv, batch, rep, native=True)
                    )
        for r in native_rows:
            r["embedder_name"] = native_payload["meta"]["embedder"]
        write_jsonl(p.out / "cli_native.jsonl", native_rows)


# ------------------------------------------------------------------ mcp ----


def cmd_mcp(p, args):
    rows = []
    bench = ROOT / "scripts/mcp_bench.py"
    for rep in range(args.reps):
        for batch in range(2):
            for name in p.corpora:
                repo = p.idx / name
                # First call: one fresh server per (tool, sample), so every
                # sample is a genuine cache miss that loads the corpus.
                for tool in ("search", "query"):
                  for tag, oxide, _ in p.ordered(rep):
                    r = sh(
                        [sys.executable, bench, oxide, str(repo), QUERIES[name], "1", "--json", "--tools", tool],
                        env=hashed_env(),
                    )
                    d = json.loads(r.stdout)
                    rows.append(
                        {
                            "kind": "mcp_first",
                            "binary": tag,
                            "corpus": name,
                            "tool": tool,
                            "batch": batch,
                            "rep": rep,
                            "ms": d["samples_ms"][tool][0],
                            "rss_kb": d["rss_kb"],
                            "vm_hwm_kb": d["vm_hwm_kb"],
                        }
                    )
            print(f"mcp first-call rep {rep} batch {batch} done")
    # Steady state: one server, 1 + N calls per tool; call 0 is the miss.
    for batch in range(2):
        for name in p.corpora:
          for tag, oxide, _ in p.ordered(batch):
            repo = p.idx / name
            r = sh(
                [sys.executable, bench, oxide, str(repo), QUERIES[name], str(args.steady + 1), "--json"],
                env=hashed_env(),
            )
            d = json.loads(r.stdout)
            for tool, samples in d["samples_ms"].items():
                for i, ms in enumerate(samples):
                    rows.append(
                        {
                            "kind": "mcp_steady",
                            "binary": tag,
                            "corpus": name,
                            "tool": tool,
                            "batch": batch,
                            "call": i,
                            "ms": ms,
                            "rss_kb": d["rss_kb"],
                            "vm_hwm_kb": d["vm_hwm_kb"],
                        }
                    )
        print(f"mcp steady batch {batch} done")
    write_jsonl(p.out / "mcp.jsonl", rows)


def cmd_mcp_servers(p, args):
    """Warm-MCP latency over *independent* servers: the unit is one fresh
    `oxide mcp` process, not one call. Per batch, every (corpus, binary,
    server slot) is run in a seeded random order after one discarded
    page-cache warmup server per (corpus, binary). Each server makes the
    cache-miss call 0, then `--warmup` more calls, then `--steady` measured
    calls per tool; all calls are written, warmup ones flagged."""
    import random

    rows = []
    bench = ROOT / "scripts/mcp_bench.py"
    calls = 1 + args.warmup + args.steady

    def serve(oxide, name):
        r = sh([sys.executable, bench, oxide, str(p.idx / name), QUERIES[name], str(calls), "--json"],
               env=hashed_env())
        return json.loads(r.stdout)

    for batch in range(2):
        for name in p.corpora:
            for _, oxide, _ in p.variants:
                serve(oxide, name)  # page-cache warmup, not recorded
        schedule = [(name, tag, oxide, k) for name in p.corpora for tag, oxide, _ in p.variants
                    for k in range(args.servers)]
        random.Random(args.seed * 10 + batch).shuffle(schedule)
        for order, (name, tag, oxide, k) in enumerate(schedule):
            d = serve(oxide, name)
            for tool, samples in d["samples_ms"].items():
                for i, ms in enumerate(samples):
                    rows.append({
                        "kind": "mcp_server", "binary": tag, "corpus": name, "tool": tool,
                        "batch": batch, "server": k, "order": order, "call": i,
                        "warmup": i <= args.warmup, "ms": ms,
                        "rss_kb": d["rss_kb"], "vm_hwm_kb": d["vm_hwm_kb"],
                    })
        print(f"mcp-servers batch {batch} done")
    write_jsonl(p.out / "mcp_servers.jsonl", rows)
    # Provenance: which executable each tag is, and the exact design.
    (p.out / "mcp_servers_manifest.json").write_text(json.dumps({
        "variants": {tag: {"oxide": str(oxide), "oxide_sha256": sha256(oxide)} for tag, oxide, _ in p.variants},
        "corpora": p.corpora, "servers_per_batch": args.servers, "warmup_calls": args.warmup,
        "measured_calls": args.steady, "seed": args.seed, "batches": 2,
        "argv": sys.argv, "oxide_commit": oxide_commit(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }, indent=2) + "\n")


# --------------------------------------------------------------- parity ----


def parity_matrix(p, binary, tag):
    """Every deterministic retrieval output for one binary, hashed:
    per corpus 8 queries × 11 surfaces, 11 literal patterns, one `review`,
    and 8 queries × 2 MCP tools × (cold, cached) response bodies."""
    env = hashed_env()
    digests = {}
    for name in p.corpora:
        repo = p.idx / name
        for q in PARITY_QUERIES:
            for surface, argv in SEARCH_SURFACES.items():
                r = sh([binary, *argv, "--json", "--path", str(repo), q], env=env)
                digests[f"{name}|{surface}|{q}"] = hashlib.sha256(r.stdout.encode()).hexdigest()
        for pat in LITERAL_PATTERNS:
            r = sh([binary, "search", "-m", "literal", "--json", "--path", str(repo), pat], env=env)
            digests[f"{name}|literal|{pat}"] = hashlib.sha256(r.stdout.encode()).hexdigest()
        # `review` of the worktree-vs-HEAD diff (empty on a clean clone; the
        # lean-snapshot parity copies carry a synthetic one), and the MCP
        # response bodies of a cold then a cached-snapshot call per tool.
        # Must succeed: two binaries failing alike would otherwise "match".
        r = sh([binary, "review", "--diff", "", "--json", "--path", str(repo)], env=env)
        digests[f"{name}|review|"] = hashlib.sha256(r.stdout.encode()).hexdigest()
        for q in PARITY_QUERIES:
            r = sh([sys.executable, str(ROOT / "scripts" / "mcp_bench.py"), binary, str(repo), q, "2",
                    "--json", "--bodies"], env=env)
            for tool, hashes in json.loads(r.stdout)["bodies"].items():
                for i, h in enumerate(hashes):
                    digests[f"{name}|mcp-{tool}-call{i}|{q}"] = h
    (p.out / f"parity_{tag}.json").write_text(json.dumps(digests, indent=1, sort_keys=True) + "\n")
    return digests


def cmd_parity(p, args):
    """`--baseline-bin` is a release `oxide` built from the commit the
    harness change sits on; `--oxide` is the harness-change build. Both are
    run twice: baseline-vs-itself proves determinism, baseline-vs-harness
    proves the harness changed no output."""
    p.out.mkdir(parents=True, exist_ok=True)
    base = Path(args.baseline_bin).resolve() if args.baseline_bin else p.oxide
    a = parity_matrix(p, base, "baseline_run1")
    b = parity_matrix(p, base, "baseline_run2")
    c = parity_matrix(p, p.oxide, "harness")
    summary = {
        "outputs": len(a),
        "retrieval_outputs": sum(1 for k in a if "|literal|" not in k),
        "literal_outputs": sum(1 for k in a if "|literal|" in k),
        "baseline_binary": str(base),
        "baseline_sha256": sha256(base),
        "harness_binary": str(p.oxide),
        "harness_sha256": sha256(p.oxide),
        "baseline_vs_itself_diffs": [k for k in a if a[k] != b[k]],
        "baseline_vs_harness_diffs": [k for k in a if a[k] != c[k]],
    }
    (p.out / "parity_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if "diffs" in k or "outputs" in k}))


# --------------------------------------------------------------- report ----


def out_label(path):
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def fmt_ms(xs):
    if not xs:
        return "—"
    lo, hi = spread(xs)
    return f"{median(xs):.1f} ({lo:.1f}–{hi:.1f})"


def fmt_int(xs):
    if not xs:
        return "—"
    lo, hi = spread(xs)
    m = median(xs)
    return f"{m:,.0f}" if lo == hi else f"{m:,.0f} ({lo:,}–{hi:,})"


def cmd_report(p, args):
    manifest = json.loads((p.out / "manifest.json").read_text())
    # The report describes one binary: challenger rows (`binary`, from an
    # interleaved base/challenger sweep) are compared elsewhere, never
    # pooled into these medians or their batch A/B noise.
    def base_only(rows):
        return [r for r in rows if r.get("binary", "base") == "base"]

    stages = base_only(read_jsonl(p.out / "stages.jsonl"))
    cli = base_only(read_jsonl(p.out / "cli.jsonl"))
    cli_native = read_jsonl(p.out / "cli_native.jsonl")
    mcp = base_only(read_jsonl(p.out / "mcp.jsonl"))
    idx = read_jsonl(p.out / "index_costs.jsonl")
    parity = p.out / "parity_summary.json"
    parity = json.loads(parity.read_text()) if parity.exists() else None
    run_ids = {}
    for label, rows in (("stages", stages), ("cli", cli), ("cli_native", cli_native), ("mcp", mcp), ("index_costs", idx)):
        run_ids[label] = sorted({r.get("run_id", "<none>") for r in rows})
    L = []
    L.append("# Corpus-load baseline — generated summary\n")
    L.append(f"Generated by `scripts/corpus_load_baseline.py report` from `{out_label(p.out)}` "
             f"on {time.strftime('%Y-%m-%d')}. OXIDE `{manifest['oxide_commit'][:10]}`, "
             f"{manifest['rustc']}, rusqlite {manifest['rusqlite']} / libsqlite3-sys {manifest['libsqlite3_sys']}, "
             f"{manifest['cpu']} ({manifest['cpu_count']} CPUs), {manifest['platform']}.\n")
    L.append("Run ids per raw file (the manifest's is `" + manifest.get("run_id", "<none>") + "`): "
             + "; ".join(f"{k}: {', '.join(v) or '—'}" for k, v in run_ids.items()) + ".\n")
    L.append("## Corpora (measured)\n")
    L.append("| corpus | commit | files | symbols | index.db | refs items / bytes | imports items / bytes (distinct) | relations rows | refs_json p50/p90/p99/max B |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for name, c in manifest["corpora"].items():
        pl = c["payload"]
        L.append(
            f"| {name} | `{c['commit'][:10]}` | {pl['files']} | {pl['symbols']:,} | {c['index_db_bytes']/1e6:.1f} MB "
            f"| {pl['references_items']:,} / {pl['references_json_bytes']/1e6:.2f} MB "
            f"| {pl['imports_items']:,} / {pl['imports_json_bytes']/1e6:.2f} MB ({pl['imports_json_distinct']}) "
            f"| {pl['symbol_relations_rows']:,} | {pl['references_json_bytes_p50']}/{pl['references_json_bytes_p90']}/{pl['references_json_bytes_p99']}/{pl['references_json_bytes_max']} |"
        )
    L.append("")

    def stage_rows(name, stage, cold):
        return [r for r in stages if r["corpus"] == name and r["stage"] == stage and r["cold"] == cold]

    L.append("## Isolated stages (one process per sample; `rep 0` = process-cold, later reps = warmed)\n")
    L.append("Instrumented `retrieval_profile` binary (counting allocator). Time is median (min–max) over both batches; per-symbol values divide by the corpus symbol count. Overlapping totals (`snapshot_load` ⊃ `all_symbols` + `all_symbol_relations`; `context` ⊃ everything) are listed side by side, not summed.\n")
    L.append("| corpus | stage | cold ms | warm ms | batch A / B warm medians (Δ%) | allocs | bytes | µs/sym (warm) | allocs/sym | B/sym | VmHWM KB |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name in manifest["corpora"]:
        n = manifest["corpora"][name]["payload"]["symbols"]
        for stage in STAGES:
            cold = stage_rows(name, stage, True)
            warm = stage_rows(name, stage, False)
            if not cold:
                continue
            allocs = [r["allocs"] for r in cold + warm]
            byts = [r["bytes"] for r in cold + warm]
            wm = median([r["ms"] for r in warm]) if warm else median([r["ms"] for r in cold])
            hwm = [r["vm_hwm_kb"] for r in cold + warm if r.get("vm_hwm_kb")]
            ab = [median([r["ms"] for r in (warm or cold) if r["batch"] == b]) for b in (0, 1)]
            ab_s = "—" if None in ab else f"{ab[0]:.1f} / {ab[1]:.1f} ({(ab[1] - ab[0]) / ab[0] * 100:+.0f}%)"
            L.append(
                f"| {name} | `{stage}` | {fmt_ms([r['ms'] for r in cold])} | {fmt_ms([r['ms'] for r in warm])} | {ab_s} "
                f"| {fmt_int(allocs)} | {fmt_int(byts)} | {wm*1000/n:.2f} | {median(allocs)/n:.1f} | {median(byts)/n:.0f} | {fmt_int(hwm)} |"
            )
    L.append("")
    notes = {}
    for r in stages:
        if r["stage"] in ("search_expand", "search_noexpand", "context") and r["cold"]:
            notes.setdefault((r["corpus"], r["stage"]), set()).add(r["note"])
    # Noise: the largest batch A vs B median disagreement per class, so
    # the README's band is read off the data rather than asserted.
    def ab_delta(rs):
        a = [r["ms"] for r in rs if r["batch"] == 0]
        b = [r["ms"] for r in rs if r["batch"] == 1]
        return (median(b) - median(a)) / median(a) * 100 if a and b else None

    L.append("### Batch A vs B disagreement (median of B vs median of A, per row)\n")
    L.append("| class | rows | max abs Δ% | row at the max | rows with abs Δ > 5% |")
    L.append("|---|---:|---:|---|---:|")
    classes = {
        "large corpora, warm, stage ≥ 5 ms": lambda r, med: r["corpus"] in ("pytest", "pylint") and not r["cold"] and med >= 5,
        "large corpora, cold, stage ≥ 5 ms": lambda r, med: r["corpus"] in ("pytest", "pylint") and r["cold"] and med >= 5,
        "large corpora, stage < 5 ms": lambda r, med: r["corpus"] in ("pytest", "pylint") and med < 5,
        "requests (all stages)": lambda r, med: r["corpus"] == "requests",
    }
    for label, pred in classes.items():
        deltas = []
        for name in manifest["corpora"]:
            for stage in STAGES:
                for cold in (True, False):
                    rs = stage_rows(name, stage, cold)
                    if not rs:
                        continue
                    med_ms = median([r["ms"] for r in rs])
                    if pred(rs[0], med_ms):
                        d = ab_delta(rs)
                        if d is not None:
                            deltas.append((abs(d), f"{name} `{stage}` {'cold' if cold else 'warm'} {d:+.1f}%"))
        if deltas:
            deltas.sort(reverse=True)
            L.append(f"| {label} | {len(deltas)} | {deltas[0][0]:.1f}% | {deltas[0][1]} | {sum(1 for d in deltas if d[0] > 5)} |")
    cli_deltas = []
    for name in ("pytest", "pylint"):
        for surface in CLI_SURFACES:
            rs = [r for r in cli if r["corpus"] == name and r["surface"] == surface]
            if rs:
                d = ab_delta(rs)
                spread_pct = (max(r["ms"] for r in rs) - min(r["ms"] for r in rs)) / median([r["ms"] for r in rs]) * 100
                cli_deltas.append((abs(d), f"{name} {surface} {d:+.1f}% (min–max spread {spread_pct:.0f}% of median)"))
    if cli_deltas:
        cli_deltas.sort(reverse=True)
        L.append(f"| one-shot CLI, large corpora | {len(cli_deltas)} | {cli_deltas[0][0]:.1f}% | {cli_deltas[0][1]} | {sum(1 for d in cli_deltas if d[0] > 5)} |")
    L.append("")
    L.append("Expansion check (`search_expand` note, every cold sample): " + "; ".join(
        f"{k[0]} {k[1]}: {', '.join(sorted(v))}" for k, v in sorted(notes.items()) if k[1] == "search_expand") + "\n")

    L.append("## One-shot CLI (uninstrumented release binary; wall clock + `ru_maxrss` via `wait4`)\n")
    L.append("| corpus | surface | ms | peak RSS KB | expanded hits | n |")
    L.append("|---|---|---:|---:|---:|---:|")
    for name in manifest["corpora"]:
        for surface in CLI_SURFACES:
            rs = [r for r in cli if r["corpus"] == name and r["surface"] == surface]
            if not rs:
                continue
            exp = sorted({r["expanded_hits"] for r in rs if r["expanded_hits"] is not None})
            L.append(f"| {name} | {surface} | {fmt_ms([r['ms'] for r in rs])} | {fmt_int([r['rss_kb'] for r in rs])} | {exp or '—'} | {len(rs)} |")
    if cli_native:
        L.append("")
        L.append(f"Native-default check (`requests`, `{cli_native[0].get('embedder_name')}`, model load included, separate index — not comparable to hashed rows):\n")
        L.append("| surface | ms | peak RSS KB |")
        L.append("|---|---:|---:|")
        for surface in CLI_SURFACES:
            rs = [r for r in cli_native if r["surface"] == surface]
            L.append(f"| {surface} | {fmt_ms([r['ms'] for r in rs])} | {fmt_int([r['rss_kb'] for r in rs])} |")
    L.append("")
    L.append("## MCP (`scripts/mcp_bench.py --json`)\n")
    L.append("| corpus | tool | first call (fresh server each) ms | steady ms (calls 1..N, one server) | server RSS KB (steady) |")
    L.append("|---|---|---:|---:|---:|")
    for name in manifest["corpora"]:
        for tool in ("search", "query"):
            first = [r["ms"] for r in mcp if r["kind"] == "mcp_first" and r["corpus"] == name and r["tool"] == tool]
            steady = [r["ms"] for r in mcp if r["kind"] == "mcp_steady" and r["corpus"] == name and r["tool"] == tool and r["call"] > 0]
            rss = [r["rss_kb"] for r in mcp if r["kind"] == "mcp_steady" and r["corpus"] == name]
            if first or steady:
                L.append(f"| {name} | {tool} | {fmt_ms(first)} | {fmt_ms(steady)} | {fmt_int(rss)} |")
    L.append("")
    L.append("## Index and edit costs (isolated copies, hashed embedder)\n")
    L.append("| corpus | op | ms | peak RSS KB | db bytes | WAL peak bytes | WAL final bytes | n |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for name in manifest["corpora"]:
        for op in ("cold_index", "nochange_reindex", "single_file_edit"):
            rs = [r for r in idx if r["corpus"] == name and r["op"] == op]
            if not rs:
                continue
            L.append(
                f"| {name} | {op} | {fmt_ms([r['ms'] for r in rs])} | {fmt_int([r['rss_kb'] for r in rs])} "
                f"| {fmt_int([r['db_bytes'] for r in rs])} | {fmt_int([r['wal_peak_bytes'] for r in rs])} | {fmt_int([r['wal_final_bytes'] for r in rs])} | {len(rs)} |"
            )
    L.append("")
    if parity:
        L.append("## Parity\n")
        L.append(
            f"{parity['outputs']} outputs ({parity['retrieval_outputs']} retrieval + {parity['literal_outputs']} literal). "
            f"Baseline vs itself: {len(parity['baseline_vs_itself_diffs'])} diffs. Baseline vs harness binary: "
            f"{len(parity['baseline_vs_harness_diffs'])} diffs. Baseline sha256 `{parity['baseline_sha256'][:16]}…`, "
            f"harness sha256 `{parity['harness_sha256'][:16]}…`.\n"
        )
    (p.out.parent / "summary.md").write_text("\n".join(L) + "\n")
    print(f"wrote {p.out.parent / 'summary.md'}")


# ----------------------------------------------------------------- main ----


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "command",
        choices=["all", "setup", "manifest", "index-costs", "stages", "cli", "mcp", "mcp-servers", "parity",
                 "report"],
    )
    ap.add_argument("--work", required=True, help="scratch directory for clones, indexed copies and binaries")
    ap.add_argument("--out", default=str(OUT_DEFAULT), help="raw sample directory (default: the committed one)")
    ap.add_argument("--oxide", default=str(ROOT / "target/release/oxide"))
    ap.add_argument("--profile", default=str(ROOT / "target/release/examples/retrieval_profile"))
    ap.add_argument("--baseline-bin", default=None, help="parity: release oxide built from the base commit")
    ap.add_argument("--corpora", default=None, help="comma-separated subset of corpora")
    ap.add_argument("--challenger-oxide", default=None,
                    help="stages/cli/mcp: a second oxide build, interleaved with --oxide in every batch")
    ap.add_argument("--challenger-profile", default=None,
                    help="stages: a second retrieval_profile build, interleaved with --profile")
    ap.add_argument("--servers", type=int, default=10,
                    help="mcp-servers: independent servers per binary per corpus per batch")
    ap.add_argument("--warmup", type=int, default=3,
                    help="mcp-servers: calls after the cache-miss call 0 discarded as warmup")
    ap.add_argument("--seed", type=int, default=0, help="mcp-servers: schedule shuffle seed")
    ap.add_argument("--aa-control", action="store_true",
                    help="mcp-servers: add the baseline again as variant `base_aa` (same-schedule A/A)")
    ap.add_argument("--git-surfaces", action="store_true",
                    help="cli: also time `query --git` and `review` (needs a working-tree diff)")
    ap.add_argument("--reps", type=int, default=5, help="samples per batch (two interleaved batches)")
    ap.add_argument("--in-process", type=int, default=3, help="stage repetitions inside one process (rep 0 is cold)")
    ap.add_argument("--steady", type=int, default=16, help="MCP steady-state calls per tool after the first")
    ap.add_argument("--stages", default=None, help="stages: comma-separated subset of STAGES (appends to stages.jsonl)")
    ap.add_argument("--skip-native", action="store_true")
    args = ap.parse_args()
    p = Paths(args)
    p.out.mkdir(parents=True, exist_ok=True)
    steps = {
        "setup": cmd_setup,
        "manifest": cmd_manifest,
        "index-costs": cmd_index_costs,
        "stages": cmd_stages,
        "cli": cmd_cli,
        "mcp": cmd_mcp,
        "mcp-servers": cmd_mcp_servers,
        "parity": cmd_parity,
        "report": cmd_report,
    }
    if args.command == "all":
        for name in ["setup", "index-costs", "stages", "cli", "mcp", "parity", "report"]:
            steps[name](p, args)
    else:
        steps[args.command](p, args)


if __name__ == "__main__":
    main()
