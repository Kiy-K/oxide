"""fts_nonindexed.py SCRATCH REPO [REPO ...]: Part C, does FTS stay exact when only
non-indexed columns change?

What a layout/body edit changes in ``Entity``: the edited file's spans, never a
``document()``. Like ``indexing-throughput/scripts/fts_probe.py`` (whose helpers are
reused), but the table has a non-indexed ``pos`` column and the mutation only SETs ``pos``
on 5% of rows. The mutated copy is compared with a fresh build over the final rows on
every query's exact ``(key, score)`` list.
"""

from __future__ import annotations

import json
import random
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "indexing-throughput/scripts"))
from fts_probe import INDEX, connect, search

from oxide.capture import capture
from oxide.core.indexing import derive
from oxide.core.retrieval import document

SCHEMA = "CREATE NODE TABLE Entity(key STRING PRIMARY KEY, terms STRING, pos INT64)"


def build(path: Path, rows: dict[str, str], pos: dict[str, int]) -> None:
    db, conn = connect(path)
    conn.execute(SCHEMA)
    for key, terms in rows.items():
        conn.execute(
            "CREATE (:Entity {key: $k, terms: $t, pos: $p})", {"k": key, "t": terms, "p": pos[key]}
        )
    conn.execute(INDEX)
    conn.execute("CHECKPOINT")
    conn.close()
    db.close()


def main() -> None:
    scratch = Path(sys.argv[1])
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    rows = {
        f"{repo}:{e.key}": terms
        for repo in sys.argv[2:]
        for e in derive(capture(repo), "probe").entities
        if (terms := document(e))
    }
    rng = random.Random(7)
    keys = sorted(rows)
    moved = set(rng.sample(keys, len(keys) // 20))
    before = dict.fromkeys(keys, 0)
    after = {k: int(k in moved) for k in keys}
    build(scratch / "a.lbug", rows, before)
    shutil.copy(scratch / "a.lbug", scratch / "a2.lbug")  # copy-on-write generation
    db, conn = connect(scratch / "a2.lbug")
    conn.execute("BEGIN TRANSACTION")
    for k in sorted(moved):
        conn.execute("MATCH (e:Entity {key: $k}) SET e.pos = 1", {"k": k})
    conn.execute("COMMIT")
    conn.execute("CHECKPOINT")
    build(scratch / "b.lbug", rows, after)
    db_b, conn_b = connect(scratch / "b.lbug")
    words = sorted({w for v in rows.values() for w in v.split()})
    queries = rng.sample(words, min(400, len(words)))
    queries += [" ".join(rng.sample(words, 3)) for _ in range(100)]
    differ = [q for q in queries if search(conn, q) != search(conn_b, q)]
    stored = conn.execute("MATCH (e:Entity) RETURN e.key, e.pos ORDER BY e.key").get_all()
    print(
        json.dumps({
            "rows": len(rows),
            "set_rows": len(moved),
            "queries": len(queries),
            "score_lists_differ": len(differ),
            "first": differ[:1],
            "pos_equal": [tuple(r) for r in stored] == sorted(after.items()),
        })
    )  # fmt: skip
    for c in (conn, conn_b):
        c.close()
    db.close()
    db_b.close()
    shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
