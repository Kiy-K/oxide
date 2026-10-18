"""Bounded JavaScript milestone evidence, without running repository code.

real ROOT OUT: coverage, resolutions, facts parity and persisted edit/rebuild parity.
context ROWS_JSON CORPORA OUT: independently audit JS gold formerly outside the TS
corpus, then use the existing ContextBench scorer on fresh mixed-source corpora.
ROWS_JSON is a subset of the SHA-checked full.parquet, not a new gold source.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import resource
import subprocess
import sys
import tarfile
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests/python"))
import contextbench as cb
import contextbench_js as js
import contextbench_ts as ts

import oxide
from oxide.capture import capture
from oxide.core.indexing import derive, validate


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n")


def real(root: Path, out: Path) -> None:
    from test_incremental import dump

    snap = capture(root)
    started = time.perf_counter()
    full = derive(snap, "evaluation")
    cold = time.perf_counter() - started
    validate(full)
    started = time.perf_counter()
    warm = derive(snap, "evaluation", full.facts)
    reused = time.perf_counter() - started
    assert warm == full
    js_files = [f for f in full.files if f.language == "javascript"]
    resolutions = Counter((e.kind, e.resolution) for e in full.edges)
    sample = [e for e in full.edges if e.kind == "imports" and e.resolution == "resolved"][:20]
    # Only the owned temporary corpus is edited; restore its exact bytes on exit.
    edit = next(root / f.path for f in js_files if f.coverage == "complete")
    original = edit.read_bytes()
    with tempfile.TemporaryDirectory(prefix="oxide-js-eval-") as temporary:
        data = Path(temporary)
        copy = data / "repo"
        for f in snap.files:
            target = copy / f.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f.data)
        edit = copy / edit.relative_to(root)
        with oxide.open_repository(copy, data_dir=data / "incremental") as repo:
            report = repo.index()
            unchanged = repo.index()
            try:
                edit.write_bytes(original + b"\n// OXIDE incremental evidence\n")
                edited = repo.index()
                with oxide.open_repository(copy, data_dir=data / "full") as fresh:
                    fresh.index()
                    assert dump(repo) == dump(fresh)
            finally:
                edit.write_bytes(original)
    from dataclasses import asdict

    save(
        out,
        {
            "repository": str(root),
            "commit": subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip(),
            "snapshot": snap.snapshot_id,
            "files": len(full.files),
            "js_files": len(js_files),
            "coverage": dict(Counter(f.coverage for f in js_files)),
            "entities": len(full.entities),
            "js_symbol_kinds": dict(
                Counter(
                    e.kind
                    for e in full.entities
                    if e.category == "symbol"
                    and e.source is not None
                    and e.source.path.endswith((".js", ".jsx", ".mjs", ".cjs"))
                )
            ),
            "graph_sha256": hashlib.sha256(
                json.dumps(
                    [[asdict(e) for e in full.entities], [asdict(e) for e in full.edges]],
                    sort_keys=True,
                ).encode()
            ).hexdigest(),
            "relations": len(full.edges),
            "resolution": {f"{k}:{r}": n for (k, r), n in sorted(resolutions.items())},
            "derive_cold_s": cold,
            "derive_reused_s": reused,
            "facts_reused": warm.reused,
            "derive_parity": warm == full,
            "index_cold_s": report.seconds,
            "index_unchanged_s": unchanged.seconds,
            "index_edit_s": edited.seconds,
            "edit_reused": edited.reused,
            "persisted_edit_parity": True,
            "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "resolved_import_sample": [asdict(e) for e in sample],
        },
    )


def context(rows_path: Path, corpora: Path, out: Path) -> None:
    rows = json.loads(rows_path.read_text())
    historical = json.loads(ts.TASKS.read_text())
    tasks = {t["id"]: t for t in historical["tasks"]}
    results, audits, identities, times = [], {}, {}, {}
    for row in rows:
        task = tasks[row["original_inst_id"]]
        assert row["base_commit"] == historical["corpora"][task["corpus"]]["commit"]
        root = corpora / task["corpus"]
        gold, verdicts = [], []
        for g, original in zip(task["gold"], json.loads(row["gold_context"]), strict=True):
            if not g["file"].endswith((".js", ".jsx", ".mjs", ".cjs")):
                continue
            assert cb._sha(cb.normalized(original["content"])) == g["sha256"]
            content, numbered = ts.unnumbered(original["content"], g["start"])
            verdict = cb.classify(root, g["file"], g["start"], g["end"], content, row["patch"])
            verdicts.append(verdict | {"numbered": numbered})
            gold.append(
                {
                    **g,
                    "file": verdict.get("corrected_file", g["file"]),
                    "class": verdict["class"],
                    "verified": verdict["class"] in cb.VERIFIED,
                }
            )
        audits[task["id"]] = verdicts
        with (
            tempfile.TemporaryDirectory(prefix="oxide-contextbench-js-") as temporary,
            oxide.open_repository(root, data_dir=Path(temporary)) as repo,
        ):
            report = repo.index()
            started = time.perf_counter()
            result = cb.score(repo, js.Lines(root), {**task, "gold": gold})
            assert all(
                b["within_budget"] and b["deterministic"] for b in result["bundles"].values()
            )
            results.append(result)
            times[task["id"]] = {
                "index_s": report.seconds,
                "score_s": time.perf_counter() - started,
            }
            identities[task["corpus"]] = {
                "snapshot": report.snapshot.snapshot_id,
                "commit": row["base_commit"],
                "files": report.files,
                "entities": report.entities,
                "relations": report.relations,
                "partial": list(report.partial),
            }
    save(out / "results.json", results)
    save(out / "metrics.json", cb.aggregate(results))
    save(out / "gold-audit.json", audits)
    save(
        out / "manifest.json",
        {
            "source_sha256": cb.PARQUET_SHA256,
            "historical_tasks_sha256": hashlib.sha256(ts.TASKS.read_bytes()).hexdigest(),
            "selection": list(audits),
            "corpora": identities,
            "budgets": cb.BUDGETS,
            "scorer": "contextbench-py-scorer with native-JS module projection v1",
        },
    )
    save(
        out / "performance.json",
        {"tasks": times, "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024},
    )


SELECTED = ("mui__material-ui-11858", "mui__material-ui-11987", "mui__material-ui-12236")


def fetch(rows_path: Path, directory: Path) -> None:
    """Owned evaluation directory only; never change the historical corpus/cache."""
    for row in json.loads(rows_path.read_text()):
        commit = row["base_commit"]
        name = row["repo"].replace("/", "__") + "@" + commit[:12]
        target = directory / name
        if target.exists():
            raise SystemExit(f"refusing to overwrite {target}")
        with tempfile.TemporaryDirectory(prefix="oxide-js-git-") as temporary:
            subprocess.run(["git", "init", "-q", "--bare", temporary], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    temporary,
                    "fetch",
                    "-q",
                    "--depth",
                    "1",
                    "https://github.com/" + row["repo"],
                    commit,
                ],
                check=True,
            )
            archive = subprocess.check_output(["git", "-C", temporary, "archive", commit])
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            members = [
                m
                for m in tar
                if m.isfile()
                and (
                    m.name.endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"))
                    or Path(m.name).name == "package.json"
                    or ts.is_config(m.name)
                )
            ]
            if len(members) > 7500:
                raise SystemExit(f"{name}: more than 7500 source/config files")
            for member in members:
                path = Path(member.name)
                assert not path.is_absolute() and ".." not in path.parts
                stream = tar.extractfile(member)
                assert stream is not None
                dest = target / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(stream.read())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    real_parser = sub.add_parser("real")
    real_parser.add_argument("root", type=Path)
    real_parser.add_argument("out", type=Path)
    context_parser = sub.add_parser("context")
    context_parser.add_argument("rows", type=Path)
    context_parser.add_argument("corpora", type=Path)
    context_parser.add_argument("out", type=Path)
    rows_parser = sub.add_parser("rows")
    rows_parser.add_argument("parquet", type=Path)
    rows_parser.add_argument("out", type=Path)
    fetch_parser = sub.add_parser("fetch")
    fetch_parser.add_argument("rows", type=Path)
    fetch_parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.mode == "real":
        real(args.root, args.out)
    elif args.mode == "context":
        context(args.rows, args.corpora, args.out)
    elif args.mode == "fetch":
        fetch(args.rows, args.directory)
    else:
        rows = [r for r in cb._parquet(args.parquet) if r["original_inst_id"] in SELECTED]
        assert len(rows) == len(SELECTED)
        save(args.out, rows)
