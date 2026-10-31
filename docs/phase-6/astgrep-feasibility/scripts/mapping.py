"""Study C (identity): can every ast-grep match become OXIDE evidence?

Indexes a corpus with OXIDE into a private data directory, runs every query of its
language with ast-grep over the *retained source* of the published generation (the
bytes OXIDE serves, read through `View.source`), converts each match to a byte
`SourceRef`, and maps it to its innermost declaration with `View.innermost`. Reports
matches whose byte slice differs from the node text (must be 0), and how many map to
a symbol vs module level.

    mapping.py CORPUS_NAME DATA_DIR OUT_DIR
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import arms
import oxide
from oxide.models import SourceRef
from queries import QUERIES
from structural_eval import CACHE, CORPORA


def main() -> None:
    name, data_dir, out_dir = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    rel, lang = CORPORA[name]
    counts: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    with oxide.open_repository(CACHE / rel, data_dir=data_dir) as repo:
        repo.index()
        view = repo._view()
        for info in view.files.values():
            grammar = arms.grammar(info.path)
            if grammar is None or (grammar == "python") != (lang == "python"):
                continue
            data = view.source(SourceRef(info.path, 0, 0, info.digest))
            text = arms.decode(data)[0]
            root, table = arms.astgrep_root(text, grammar), arms.byte_offsets(text)
            for q in (q for q in QUERIES if q.lang == lang):
                for node in root.find_all(q.rule):
                    r = node.range()
                    start, end = r.start.index, r.end.index
                    if table is not None:
                        start, end = table[start], table[end]
                    counts["matches"] += 1
                    if data[start:end].decode("utf-8", "replace") != node.text():
                        counts["span_mismatch"] += 1
                    key = view.innermost(info.path, start, end)
                    counts["symbol" if key else "module_level"] += 1
                    if key:
                        kinds[view.entities([key])[key].kind] += 1
    report = {"corpus": name, "counts": dict(counts), "innermost_kinds": dict(kinds)}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"mapping-{name}.json").write_text(json.dumps(report, indent=1) + "\n")
    print(report, file=sys.stderr)


if __name__ == "__main__":
    main()
