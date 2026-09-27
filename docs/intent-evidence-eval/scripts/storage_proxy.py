#!/usr/bin/env python3
"""Storage proxy for a hypothetical evidence lane (issue #30). A proxy, not a
schema proposal: per corpus, write kept evidence into a scratch SQLite file
with provenance + text, one 384-d f32 vector blob per record (random bytes of
the production vector size) and a (term, id, tf) postings table, VACUUM, and
compare its size with the corpus's index.db.

usage: storage_proxy.py <inventory_corpora.txt> <ev_dir> <scratch_dir>
"""
import collections
import json
import os
import re
import sqlite3
import sys


def tok(t):
    return [p.lower() for p in re.findall(r"[A-Za-z0-9]+", t) if len(p) >= 2]


corpora = [l.split() for l in open(sys.argv[1]) if l.strip()]
evdir, scratch = sys.argv[2], sys.argv[3]
print("| repo | symbols | kept evidence | evidence/symbols | index.db | + evidence store (proxy) | growth |")
print("|---|---:|---:|---:|---:|---:|---:|")
for name, root in corpora:
    kept = [r for r in map(json.loads, open(f"{evdir}/inv-{name}.jsonl")) if r["keep"]]
    db = f"{scratch}/store-{name}.db"
    if os.path.exists(db):
        os.remove(db)
    c = sqlite3.connect(db)
    c.execute("create table evidence(id integer primary key, eid text, file text, s int, e int, type text, "
              "symbol text, heading text, text text)")
    c.execute("create table evidence_vec(id integer primary key, v blob)")
    c.execute("create table evidence_postings(term text, id int, tf int, primary key(term, id)) without rowid")
    for i, r in enumerate(kept):
        c.execute("insert into evidence values(?,?,?,?,?,?,?,?,?)", (i, r["eid"], r["file"], r["start_line"],
                  r["end_line"], r["type"], r["symbol"], r["heading"], r["text"]))
        c.execute("insert into evidence_vec values(?,?)", (i, os.urandom(384 * 4)))
        for t, n in collections.Counter(tok(r["text"])).items():
            c.execute("insert into evidence_postings values(?,?,?)", (t, i, n))
    c.commit()
    c.execute("vacuum")
    c.close()
    idx = os.path.getsize(f"{root}/.oxide/index.db")
    nsym = sqlite3.connect(f"file:{root}/.oxide/index.db?mode=ro", uri=True).execute(
        "select count(*) from symbols").fetchone()[0]
    size = os.path.getsize(db)
    os.remove(db)
    print(f"| {name} | {nsym} | {len(kept)} | {len(kept)/nsym:.2f} | {idx/1e6:.1f} MB | {size/1e6:.1f} MB | "
          f"+{100*size/idx:.0f}% |")
