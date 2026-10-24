import json
import random
import sys
from pathlib import Path

a = json.loads(Path(sys.argv[1]).read_text())
b = json.loads(Path(sys.argv[2]).read_text())
rnd = random.Random(20261023)
sample = []
for p in a:
    A = {tuple(x) for x in a[p]}
    B = {tuple(x) for x in b[p]}
    print(
        p.rsplit("/", 1)[-1],
        "before",
        len(B),
        "after",
        len(A),
        "lost",
        len(B - A),
        "new",
        len(A - B),
    )
    for x in sorted(B - A)[:5]:
        print("  LOST", x)
    new = sorted(A - B)
    sample += [(p, x) for x in rnd.sample(new, min(12, len(new)))]
for p, (src, kind, dst, ep, s, e) in sample:
    text = Path(p, ep).read_bytes()[s:e].decode(errors="replace").split("\n")[0][:90]
    print(f"{kind:10} {src.split('/', 1)[-1][-45:]:45} | {text:60} -> {dst[-55:]}")
