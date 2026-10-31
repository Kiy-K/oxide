"""Upper bound: call edges gained if ``Dispatch.method`` ignored the global ``attrs`` block.

    attrs_probe.py base|noattrs CORPUS    index CORPUS, dump its resolved/ambiguous calls

Production code is untouched; ``noattrs`` empties ``Dispatch.attrs`` in this process only.
Stores and edge dumps go to ``~/Projects/oxide-eval-data/oxide-gnav2-eval`` (outside git).
"""

import json
import sys
from pathlib import Path

import oxide
from oxide.core.languages import python_dispatch as pd

REPOS = Path.home() / "Projects/oxide-eval-data/oxide-structural-trial/repos"
OUT = Path.home() / "Projects/oxide-eval-data/oxide-gnav2-eval"
CALLS = (
    "MATCH (a:Entity)-[r:Rel]->(b:Entity) WHERE r.kind = 'calls' "
    "RETURN a.key, b.key, r.resolution, r.basis, r.ev_path, r.ev_start"
)
OPAQUE = "MATCH (:Entity)-[r:Opaque]->(:Unresolved) WHERE r.kind = 'calls' RETURN count(*)"

mode, corpus = sys.argv[1], sys.argv[2]
if mode == "noattrs":
    _init = pd.Dispatch.__init__

    def _no_attrs(self: pd.Dispatch, resolver: object) -> None:
        _init(self, resolver)  # type: ignore[arg-type]
        self.attrs = set()

    pd.Dispatch.__init__ = _no_attrs  # type: ignore[method-assign]
data = OUT / f"data-{mode}"
oxide.index_repository(str(REPOS / corpus), data_dir=str(data))
with oxide.open_repository(str(REPOS / corpus), data_dir=str(data)) as repo:
    view = repo._view()
    rows = view._query(CALLS)
    unresolved = view._query(OPAQUE)[0][0]
(OUT / f"edges-{mode}-{corpus}.json").write_text(json.dumps(sorted(map(list, rows))))
print(corpus, mode, "resolved/ambiguous call edges", len(rows), "unresolved", unresolved)
