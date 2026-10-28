"""File bytes vs live pages of a generation's graph.lbug, stage by stage (README § 1).

    uv run python stages.py GRAPH.lbug SCRATCH [auto|noauto] [sorted]

Dumps the generation's four tables to CSV in the product's format, rebuilds them with
``Store._load``'s statements, and after each stage records the file size and the pages
STORAGE_INFO attributes to live columns and indexes. The difference is space no table
owns. ``noauto`` opens the database with ``auto_checkpoint=False``, so only the explicit
CHECKPOINTs write pages. ``sorted`` writes Entity rows in key order (README § 4).
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import ladybug

from oxide.storage.ladybug import _SCHEMA, _write_csv, fts_location, load_pool

DUMP = {
    "Entity": "MATCH (e:Entity) RETURN e.key, e.category, e.kind, e.name, e.signature, e.path, "
    "e.start_byte, e.end_byte, e.digest, e.test, e.terms ORDER BY offset(id(e))",
    "Unresolved": "MATCH (u:Unresolved) RETURN u.key, u.name ORDER BY offset(id(u))",
    "Rel": "MATCH (a)-[r:Rel]->(b) RETURN a.key, b.key, r.kind, r.resolution, r.basis, r.name, "
    "r.site, r.ev_path, r.ev_start, r.ev_end ORDER BY r.site, b.key",
    "Opaque": "MATCH (a)-[r:Opaque]->(b) RETURN a.key, b.key, r.kind, r.basis, r.name, r.site, "
    "r.ev_path, r.ev_start, r.ev_end ORDER BY r.site, b.key",
}
FTS = "CALL CREATE_FTS_INDEX('Entity', 'lexical', ['terms'], stemmer := 'porter')"
CSV = "(header=false, quote='\"', escape='\"', delim=',', parallel=false)"
TABLES = ("Entity", "Unresolved", "Rel", "Opaque")
INTERNAL = ("0_lexical_docs", "0_lexical_terms", "0_lexical_appears_in")


def rows(conn: ladybug.Connection, query: str) -> list[Any]:
    return conn.execute(query).get_all()  # type: ignore[union-attr]


def dump(graph: Path, out: Path) -> None:
    db = ladybug.Database(str(graph), read_only=True, max_num_threads=1)
    conn = ladybug.Connection(db)
    for table, query in DUMP.items():
        _write_csv(out / f"{table}.csv", map(tuple, rows(conn, query)))
    conn.close()
    db.close()


def live(conn: ladybug.Connection, fts: bool) -> dict[str, int]:
    names = TABLES + (INTERNAL if fts else ())
    return {
        t: 4096 * sum(r[0] for r in rows(conn, f"CALL STORAGE_INFO('{t}') RETURN num_pages"))
        for t in names
    }


def main() -> None:
    graph, scratch = Path(sys.argv[1]), Path(sys.argv[2])
    auto = (sys.argv[3] if len(sys.argv) > 3 else "auto") == "auto"
    if sys.argv[4:] == ["sorted"]:
        DUMP["Entity"] = DUMP["Entity"].replace("ORDER BY offset(id(e))", "ORDER BY e.key")
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    dump(graph, scratch)
    path = scratch / "graph.lbug"
    pool = load_pool(sum((scratch / f"{t}.csv").stat().st_size for t in TABLES))
    db = ladybug.Database(str(path), buffer_pool_size=pool, max_num_threads=2, auto_checkpoint=auto)
    conn = ladybug.Connection(db)
    out: list[dict[str, Any]] = []

    def stage(name: str, fts: bool = False) -> None:
        pages = live(conn, fts)
        out.append(
            {
                "stage": name,
                "file": path.stat().st_size,
                "live": sum(pages.values()),
                "tables": pages,
                "t": round(time.perf_counter() - began, 3),
            }
        )

    began = time.perf_counter()
    for statement in _SCHEMA:
        rows(conn, statement)
    rows(conn, "BEGIN TRANSACTION")
    for t in TABLES:
        rows(conn, f"COPY {t} FROM '{scratch / (t + '.csv')}' {CSV}")
    rows(conn, "COMMIT")
    rows(conn, "CHECKPOINT")
    stage("copy+checkpoint")
    conn.set_max_threads_for_exec(1)
    rows(conn, f"LOAD EXTENSION '{fts_location()}'")
    fts_began = time.perf_counter()
    rows(conn, FTS)
    out.append({"stage": "fts", "fts_s": round(time.perf_counter() - fts_began, 3)})
    rows(conn, "CHECKPOINT")
    stage("fts+checkpoint", fts=True)
    conn.close()
    db.close()
    out.append({"stage": "closed", "file": path.stat().st_size})
    print(json.dumps({"auto_checkpoint": auto, "stages": out}))


if __name__ == "__main__":
    main()
