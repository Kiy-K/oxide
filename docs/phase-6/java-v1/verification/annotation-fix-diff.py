"""Edges that the declaration-header scope fix changes, on one checkout (measured).

Usage: uv run --locked python annotation-fix-diff.py ROOT OLD_JAVA_PY OLD_JAVA_RESOLVE_PY
Derives ROOT with this checkout, then with the pre-fix slice loaded over the same modules.
"""

import collections
import sys
from pathlib import Path

from oxide.capture import capture
from oxide.core.indexing import derive
from oxide.core.languages import java, java_resolve

snap = capture(sys.argv[1])
new = derive(snap, "evaluation")
for module, path in ((java, sys.argv[2]), (java_resolve, sys.argv[3])):
    exec(compile(Path(path).read_text(), path, "exec"), module.__dict__)
old = derive(snap, "evaluation")
a, b = collections.Counter(old.edges), collections.Counter(new.edges)
gone, added = a - b, b - a
print(Path(sys.argv[1]).name, "edges", sum(a.values()), "removed", sum(gone.values()), "added", sum(added.values()))
for sign, edges in (("-", gone), ("+", added)):
    for e in sorted(edges, key=repr):
        assert e.evidence is not None
        print(f" {sign} {e.kind} {e.source} -> {list(e.targets)} {e.resolution} at {e.evidence.path}:{e.evidence.start}")
