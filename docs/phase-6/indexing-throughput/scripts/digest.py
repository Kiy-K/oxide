"""Content digests of the current generation, to compare two builds for equal output.

    uv run python digest.py DATA_DIR

Hashes the manifest, the facts, the retained source names, every row of every table
(all columns, total order) and the exact ``(key, score)`` lists of 300 BM25 queries
drawn from the indexed terms. ``graph.lbug`` bytes are not compared: equal rows are
the contract, not the file layout. The store's random repository id is masked.
Prints one JSON line.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import ladybug

from oxide.storage.ladybug import fts_location

TABLES = {
    "entity": "MATCH (e:Entity) RETURN e.key, e.category, e.kind, e.name, e.signature, e.path, "
    "e.start_byte, e.end_byte, e.digest, e.test, e.terms ORDER BY e.key",
    "unresolved": "MATCH (u:Unresolved) RETURN u.key, u.name ORDER BY u.key",
    "rel": "MATCH (a)-[r:Rel]->(b) RETURN r.site, b.key, a.key, r.kind, r.resolution, r.basis, "
    "r.name, r.ev_path, r.ev_start, r.ev_end ORDER BY r.site, b.key",
    "opaque": "MATCH (a)-[r:Opaque]->(b) RETURN r.site, b.key, a.key, r.kind, r.basis, r.name, "
    "r.ev_path, r.ev_start, r.ev_end ORDER BY r.site, b.key",
}


REPO_ID = ""  # random per data directory by design; masked so two stores compare


def sha(value: object) -> str:
    text = value.decode() if isinstance(value, bytes) else repr(value)
    return hashlib.sha256(text.replace(REPO_ID, "<repo>").encode()).hexdigest()[:16]


def main() -> None:
    (store,) = (Path(sys.argv[1]) / "repos").iterdir()
    gen = store / "generations" / (store / "CURRENT").read_text()
    global REPO_ID
    REPO_ID = json.loads((gen / "MANIFEST.json").read_bytes())["repo_id"]
    out: dict[str, object] = {
        n: sha((gen / n).read_bytes()) for n in ("MANIFEST.json", "facts.json")
    }
    out["sources"] = sha(sorted(p.name for p in (gen / "source").iterdir()))
    db = ladybug.Database(str(gen / "graph.lbug"), read_only=True, max_num_threads=1)
    conn = ladybug.Connection(db)
    for name, query in TABLES.items():
        out[name] = sha(conn.execute(query).get_all())  # type: ignore[union-attr]
    conn.execute(f"LOAD EXTENSION '{fts_location()}'")
    terms = conn.execute("MATCH (e:Entity) RETURN e.terms").get_all()  # type: ignore[union-attr]
    words = sorted({w for (t,) in terms for w in t.split() if w.isalnum()})
    step = max(len(words) // 250, 1)
    queries = words[::step][:250] + [" ".join(words[i::997][:3]) for i in range(50)]
    hits = [
        conn.execute(
            f"CALL QUERY_FTS_INDEX('Entity', 'lexical', '{q}', conjunctive := false, K := 1.2, "
            "B := 0.75) RETURN node.key, score ORDER BY score DESC, node.key"
        ).get_all()  # type: ignore[union-attr]
        for q in queries
    ]
    out["lexical"] = sha(hits)
    out["queries"] = len(queries)
    conn.close()
    db.close()
    print(json.dumps(out))


if __name__ == "__main__":
    main()
