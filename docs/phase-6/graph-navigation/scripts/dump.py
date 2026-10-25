"""Index each corpus and dump its entities (for the jedi oracle, which has no oxide).

uv run python docs/phase-6/graph-navigation/scripts/dump.py
"""

import json

from common import CORPORA, DATA, checkout, open_repo

for corpus in CORPORA:
    with open_repo(corpus) as repo:
        repo.index()
        rows = repo._view()._query(
            "MATCH (e:Entity) WHERE e.category IN ['file', 'symbol'] RETURN e.key, e.category, "
            "e.kind, e.name, e.path, e.start_byte, e.end_byte, e.test"
        )
    (DATA / f"entities-{corpus}.json").write_text(json.dumps(sorted(rows)))
    print(corpus, len(rows), checkout(corpus))
