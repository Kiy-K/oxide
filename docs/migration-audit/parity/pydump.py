"""Parity dump: OXIDE v2 Python capture + derivation in the Rust dump's line format.
Writes the captured file list to argv[2] so the Rust dump reads the same bytes."""
import sys
from pathlib import Path
from oxide.capture import capture
from oxide.core.indexing import derive

root = Path(sys.argv[1])
cap = capture(root)
Path(sys.argv[2]).write_text("".join(f.path + "\n" for f in cap.files))
d = derive(cap, "r")
span = lambda s: f"{s.path}:{s.start}-{s.end}" if s else "-"
for e in d.entities:
    name = "-" if e.category == "repository" else e.name
    print(f"E\t{e.key}\t{e.kind}\t{name}\t{e.signature or ''}\t{span(e.source)}\t{str(e.test).lower()}")
for r in d.edges:
    basis = r.basis.capitalize()
    targets = ",".join(r.targets)
    print(f"R\t{r.kind}\t{r.source}\t{r.resolution}\t{targets}\t{basis}\t{r.name if r.resolution == 'unresolved' else ''}\t{span(r.evidence)}")
