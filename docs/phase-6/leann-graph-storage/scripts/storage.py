"""Storage anatomy and LEANN-style compact variants of one OXIDE generation.

    uv run python storage.py CORPUS SCRATCH > row.json

Indexes CORPUS into SCRATCH/data (the product path), then reads its rows back and rebuilds
them into standalone LadybugDB files with schema variants. Every variant holds the same
rows; the lossless ones must answer the adjacency query identically (checked).

  A  product schema + FTS (control; must be close to the product's graph.lbug)
  B  A without the FTS index
  C  B without Entity.terms (the FTS source column: recomputable from the entity)
  D  C, Rel compact: kind/resolution/basis as INT8 codes, ev_path only where it differs
     from the source entity's path (lossless: recomputed from a.path)
  E  A, then try ``ALTER TABLE Entity DROP terms`` with the FTS index kept (LEANN: keep
     the index, drop the stored source). Records whether LadybugDB allows it.
"""

from __future__ import annotations

import json
import random
import resource
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import ladybug

import oxide
from oxide.storage.ladybug import fts_location, load_pool

ENTITY = (
    "key STRING PRIMARY KEY, category STRING, kind STRING, name STRING, signature STRING, "
    "path STRING, start_byte INT64, end_byte INT64, digest STRING, test BOOLEAN"
)
REL = (
    "kind STRING, resolution STRING, basis STRING, name STRING, site INT64, ev_path STRING, "
    "ev_start INT64, ev_end INT64"
)
REL_COMPACT = (
    "kind INT8, resolution INT8, basis INT8, name STRING, site INT64, ev_path STRING, "
    "ev_start INT64, ev_end INT64"
)
OPAQUE = (
    "kind STRING, basis STRING, name STRING, site INT64, ev_path STRING, ev_start INT64, "
    "ev_end INT64"
)
FTS = "CALL CREATE_FTS_INDEX('Entity', 'lexical', ['terms'], stemmer := 'porter')"
CSV = "(header=false, quote='\"', escape='\"', delim=',', parallel=false)"


def field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return '"' + str(value).replace('"', '""') + '"'


def write(path: Path, rows: list[tuple[Any, ...]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as out:
        for row in rows:
            out.write(",".join(field(v) for v in row) + "\n")


def size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def q(conn: ladybug.Connection, query: str, params: dict[str, Any] | None = None) -> list[Any]:
    return conn.execute(query, params or {}).get_all()  # type: ignore[union-attr]


def build(
    path: Path, entity_cols: str, rel_cols: str, tables: dict[str, Path], fts: bool
) -> dict[str, Any]:
    pool = load_pool(sum(csv.stat().st_size for csv in tables.values()))  # as the product
    db = ladybug.Database(str(path), buffer_pool_size=pool, max_num_threads=2)
    conn = ladybug.Connection(db)
    q(conn, f"CREATE NODE TABLE Entity({entity_cols})")
    q(conn, "CREATE NODE TABLE Unresolved(key STRING PRIMARY KEY, name STRING)")
    q(conn, f"CREATE REL TABLE Rel(FROM Entity TO Entity, {rel_cols})")
    q(conn, f"CREATE REL TABLE Opaque(FROM Entity TO Unresolved, {OPAQUE})")
    started = time.perf_counter()
    q(conn, "BEGIN TRANSACTION")
    for table, csv in tables.items():
        if csv.stat().st_size:
            q(conn, f"COPY {table} FROM '{csv}' {CSV}")
    q(conn, "COMMIT")
    copy = time.perf_counter() - started
    started = time.perf_counter()
    if fts:
        conn.set_max_threads_for_exec(1)
        q(conn, f"LOAD EXTENSION '{fts_location()}'")
        q(conn, FTS)
    fts_s = time.perf_counter() - started
    q(conn, "CHECKPOINT")
    conn.close()
    db.close()
    return {"bytes": size(path), "copy_s": round(copy, 3), "fts_s": round(fts_s, 3)}


def adjacency(path: Path, batches: list[list[str]], compact: bool) -> tuple[float, list[Any]]:
    """The product's adjacency queries (``View.adjacency``) over every batch: seconds, rows."""
    db = ladybug.Database(str(path), read_only=True, max_num_threads=1)
    conn = ladybug.Connection(db)
    if compact:  # codes: resolution 1 = resolved, basis 1 = heuristic
        flags = "r.resolution = 1 AND r.basis <> 1, r.basis = 1"
    else:
        flags = "r.resolution = 'resolved' AND r.basis <> 'heuristic', r.basis = 'heuristic'"
    match = "MATCH (a:Entity)-[r:Rel]->(b:Entity) WHERE "
    out = match + f"a.key IN $keys RETURN a.key, r.kind, b.key, {flags}"
    into = match + f"b.key IN $keys RETURN b.key, r.kind, a.key, {flags}"
    rows: list[Any] = []
    started = time.perf_counter()
    for keys in batches:
        for query in (out, into):
            rows.append(sorted(map(tuple, q(conn, query, {"keys": keys}))))
    elapsed = time.perf_counter() - started
    conn.close()
    db.close()
    return elapsed, rows


def text(rows: list[Any], i: int) -> int:
    return sum(len((r[i] or "").encode()) for r in rows)


def main() -> None:
    corpus, scratch = Path(sys.argv[1]), Path(sys.argv[2])
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    data = scratch / "data"
    report = oxide.index_repository(corpus, data_dir=data)
    with oxide.open_repository(corpus, data_dir=data, create=False) as repo:
        view = repo._view()
        parts = {p.name: size(p) for p in view.directory.iterdir()}
        # The full-table dumps need more than a read view's 128 MiB pool on VS Code.
        dump = ladybug.Database(
            str(view.directory / "graph.lbug"),
            read_only=True,
            buffer_pool_size=1 << 30,
            max_num_threads=1,
        )
        conn = ladybug.Connection(dump)
        ent = q(
            conn,
            "MATCH (e:Entity) RETURN e.key, e.category, e.kind, e.name, e.signature, "
            "e.path, e.start_byte, e.end_byte, e.digest, e.test, e.terms ORDER BY e.key",
        )
        unres = q(conn, "MATCH (u:Unresolved) RETURN u.key, u.name ORDER BY u.key")
        rel = q(
            conn,
            "MATCH (a:Entity)-[r:Rel]->(b:Entity) RETURN a.key, b.key, r.kind, "
            "r.resolution, r.basis, r.name, r.site, r.ev_path, r.ev_start, r.ev_end, a.path "
            "ORDER BY r.site, b.key",
        )
        opq = q(
            conn,
            "MATCH (a:Entity)-[r:Opaque]->(u:Unresolved) RETURN a.key, u.key, r.kind, "
            "r.basis, r.name, r.site, r.ev_path, r.ev_start, r.ev_end ORDER BY r.site",
        )
        conn.close()
        dump.close()
    # Column anatomy: UTF-8 bytes of every string column, the raw payload LadybugDB stores.
    ecols = ["key", "category", "kind", "name", "signature", "path", "", "", "digest", "", "terms"]
    rcols = (
        (0, "a.key"),
        (1, "b.key"),
        (2, "kind"),
        (3, "resolution"),
        (4, "basis"),
        (5, "name"),
        (7, "ev_path"),
    )
    anatomy = {
        "entity": {c: text(ent, i) for i, c in enumerate(ecols) if c},
        "rel": {c: text(rel, i) for i, c in rcols},
        "rel_ev_path_is_source_path": sum(r[7] == r[10] for r in rel),
    }
    # Degree skew: the share of edge endpoints on the top p% of entities by degree.
    deg: dict[str, int] = {}
    for r in rel:
        deg[r[0]] = deg.get(r[0], 0) + 1
        deg[r[1]] = deg.get(r[1], 0) + 1
    ranked = sorted(deg.values(), reverse=True)
    total = sum(ranked)
    hubs = {
        f"top{p}%": round(sum(ranked[: max(1, len(ranked) * p // 100)]) / total, 3)
        for p in (1, 2, 5, 10)
    }

    codes: dict[str, dict[str, int]] = {
        "kind": {},
        "resolution": {"resolved": 1},
        "basis": {"heuristic": 1},
    }

    def code(col: str, value: str) -> int:
        return codes[col].setdefault(value, len(codes[col]) + 1)

    csv = scratch / "csv"
    csv.mkdir()
    write(csv / "Entity.csv", [tuple(r) for r in ent])
    write(csv / "EntityNoTerms.csv", [tuple(r[:10]) for r in ent])
    write(csv / "Unresolved.csv", [tuple(r) for r in unres])
    write(csv / "Rel.csv", [tuple(r[:10]) for r in rel])
    write(
        csv / "RelCompact.csv",
        [
            (
                r[0],
                r[1],
                code("kind", r[2]),
                code("resolution", r[3]),
                code("basis", r[4]),
                r[5],
                r[6],
                "" if r[7] == r[10] else r[7],
                r[8],
                r[9],
            )
            for r in rel
        ],
    )
    write(csv / "Opaque.csv", [tuple(r) for r in opq])
    common = {"Unresolved": csv / "Unresolved.csv", "Opaque": csv / "Opaque.csv"}
    full = {"Entity": csv / "Entity.csv", **common, "Rel": csv / "Rel.csv"}
    lean = {"Entity": csv / "EntityNoTerms.csv", **common, "Rel": csv / "Rel.csv"}
    compact = {"Entity": csv / "EntityNoTerms.csv", **common, "Rel": csv / "RelCompact.csv"}
    terms = ENTITY + ", terms STRING"
    variants: dict[str, dict[str, Any]] = {
        "A": build(scratch / "A.lbug", terms, REL, full, True),
        "B": build(scratch / "B.lbug", terms, REL, full, False),
        "C": build(scratch / "C.lbug", ENTITY, REL, lean, False),
        "D": build(scratch / "D.lbug", ENTITY, REL_COMPACT, compact, False),
    }
    shutil.rmtree(csv)
    # E: drop the FTS source column while keeping the index.
    shutil.copy(scratch / "A.lbug", scratch / "E.lbug")
    db = ladybug.Database(str(scratch / "E.lbug"), max_num_threads=1)
    conn = ladybug.Connection(db)
    q(conn, f"LOAD EXTENSION '{fts_location()}'")
    try:
        q(conn, "ALTER TABLE Entity DROP terms")
        q(conn, "CHECKPOINT")
        hits = q(
            conn,
            "CALL QUERY_FTS_INDEX('Entity', 'lexical', 'config', conjunctive := false) "
            "RETURN count(*)",
        )
        variants["E"] = {"dropped": True, "fts_hits_after": hits[0][0]}
    except RuntimeError as error:
        variants["E"] = {"dropped": False, "error": str(error)[:200]}
    conn.close()
    db.close()
    variants["E"]["bytes"] = size(scratch / "E.lbug")

    rng = random.Random(7)
    keys = sorted(deg)
    batches = [rng.sample(keys, min(len(keys), 16)) for _ in range(300)]
    latency, answers = {}, {}
    back = {}
    for name in "ABCD":
        latency[name], rows = adjacency(scratch / f"{name}.lbug", batches, name == "D")
        if name == "D":  # decode codes back to strings for the identity check
            back = {v: k for k, v in codes["kind"].items()}
            rows = [sorted((a, back[k], b, c, h) for a, k, b, c, h in batch) for batch in rows]
        answers[name] = rows
    print(
        json.dumps(
            {
                "corpus": corpus.name,
                "entities": report.entities,
                "relations": report.relations,
                "generation": parts,
                "anatomy": anatomy,
                "rels": len(rel),
                "opaque": len(opq),
                "hub_edge_share": hubs,
                "variants": variants,
                "adjacency_s_300x16": {k: round(v, 3) for k, v in latency.items()},
                "adjacency_identical": all(answers[n] == answers["A"] for n in "BCD"),
                "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
            }
        )
    )
    shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
