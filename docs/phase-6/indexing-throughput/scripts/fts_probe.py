"""Does LadybugDB 0.21.0's FTS index stay exact under DELETE / CREATE / SET?

    uv run python fts_probe.py SCRATCH REPO [REPO ...]

Real OXIDE entity terms (``document``) from deriving REPOs. Database A is built from
the initial rows and indexed, copied to a new file (a copy-on-write generation), and
mutated: 5% deleted, 5% of terms changed, 5% new rows added. Database B is a fresh
build over the same final rows. Every query (each distinct name term, plus a few
multi-term ones) is compared as the exact ``(key, score)`` list OXIDE reads.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import sys
import time
from pathlib import Path

import ladybug

from oxide.capture import capture
from oxide.core.indexing import derive
from oxide.core.retrieval import document
from oxide.storage.ladybug import fts_location

SCHEMA = "CREATE NODE TABLE Entity(key STRING PRIMARY KEY, terms STRING)"
INDEX = "CALL CREATE_FTS_INDEX('Entity', 'lexical', ['terms'], stemmer := 'porter')"


def connect(path: Path) -> tuple[ladybug.Database, ladybug.Connection]:
    db = ladybug.Database(str(path), max_num_threads=1)
    conn = ladybug.Connection(db)
    conn.execute(f"LOAD EXTENSION '{fts_location()}'")
    return db, conn


def build(path: Path, rows: dict[str, str]) -> None:
    db, conn = connect(path)
    conn.execute(SCHEMA)
    for key, terms in rows.items():
        conn.execute("CREATE (:Entity {key: $k, terms: $t})", {"k": key, "t": terms})
    conn.execute(INDEX)
    conn.execute("CHECKPOINT")
    conn.close()
    db.close()


def search(conn: ladybug.Connection, text: str) -> list[tuple[str, float]]:
    result = conn.execute(
        f"CALL QUERY_FTS_INDEX('Entity', 'lexical', '{text}', conjunctive := false, "
        "K := 1.2, B := 0.75) RETURN node.key, score ORDER BY score DESC, node.key"
    )
    return [(r[0], r[1]) for r in result.get_all()]


def main() -> None:
    scratch = Path(sys.argv[1])
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    rows: dict[str, str] = {}
    for repo in sys.argv[2:]:
        for e in derive(capture(repo), "probe").entities:
            if terms := document(e):
                rows[f"{repo}:{e.key}"] = terms
    rng = random.Random(7)
    keys = sorted(rows)
    rng.shuffle(keys)
    n = len(keys) // 20
    added, deleted, changed = keys[:n], keys[n : 2 * n], keys[2 * n : 3 * n]
    ops = os.environ.get("OPS", "delete,set,create").split(",")
    added, deleted = (added if "create" in ops else []), (deleted if "delete" in ops else [])
    changed = changed if "set" in ops else []
    initial = {k: rows[k] for k in keys[n:]}
    final = {k: v for k, v in initial.items() if k not in deleted}
    final |= {k: rows[k] for k in added}
    donors = keys[3 * n : 4 * n]
    final |= {k: rows[d] + " probe" for k, d in zip(changed, donors, strict=False)}

    build(scratch / "a.lbug", initial)
    shutil.copy(scratch / "a.lbug", scratch / "a2.lbug")  # copy-on-write generation
    db, conn = connect(scratch / "a2.lbug")
    started = time.perf_counter()
    report: dict[str, object] = {"rows": len(initial), "delta_each": n, "ops": ops}
    try:
        conn.execute("BEGIN TRANSACTION")
        for k in deleted:
            conn.execute("MATCH (e:Entity {key: $k}) DELETE e", {"k": k})
        for k in changed:
            conn.execute("MATCH (e:Entity {key: $k}) SET e.terms = $t", {"k": k, "t": final[k]})
        for k in added:
            conn.execute("CREATE (:Entity {key: $k, terms: $t})", {"k": k, "t": final[k]})
        conn.execute("COMMIT")
        if os.environ.get("REBUILD"):  # a graph delta with the FTS index rebuilt over it
            conn.execute("CALL DROP_FTS_INDEX('Entity', 'lexical')")
            conn.execute(INDEX)
        conn.execute("CHECKPOINT")
        report["mutation"] = "accepted"
    except RuntimeError as error:
        report["mutation"] = f"refused: {error}"
    report["mutation_s"] = round(time.perf_counter() - started, 3)
    build(scratch / "b.lbug", final)
    db_b, conn_b = connect(scratch / "b.lbug")
    words = sorted({w for v in final.values() for w in v.split()})
    queries = [*rng.sample(words, min(400, len(words))), "probe"]
    queries += [" ".join(rng.sample(words, 3)) for _ in range(100)]
    differ = ranks = 0
    first: list[object] = []
    groups = {"deleted": set(deleted), "changed": set(changed), "added": set(added)}
    tally: dict[str, int] = {}
    for q in queries:
        a, b = search(conn, q), search(conn_b, q)
        if a != b:
            differ += 1
            ranks += [k for k, _ in a] != [k for k, _ in b]
            if not first:
                first = [q, a[:3], b[:3]]
            sa, sb = dict(a), dict(b)
            for side, only in (("only_mutated", sa.keys() - sb), ("only_fresh", sb.keys() - sa)):
                for k in only:
                    group = next((g for g, ks in groups.items() if k in ks), "untouched")
                    tally[f"{side}:{group}"] = tally.get(f"{side}:{group}", 0) + 1
            for k in sa.keys() & sb.keys():
                if sa[k] != sb[k]:
                    tally["score_differs"] = tally.get("score_differs", 0) + 1
    report |= {"queries": len(queries), "score_lists_differ": differ, "rankings_differ": ranks}
    report["first_difference"] = first
    report["keys"] = tally
    print(json.dumps(report))
    conn.close()
    conn_b.close()
    db.close()
    db_b.close()
    shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
