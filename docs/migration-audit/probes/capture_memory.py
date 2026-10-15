"""Scratch probe: peak RSS of capture() and index() over N MiB of synthetic files."""

import json
import os
import resource
import sys
import tempfile
from pathlib import Path

mib, mode = int(sys.argv[1]), sys.argv[2]
root = Path(tempfile.mkdtemp(prefix="oxide-mem-", dir="/tmp/oxide-parity/mem"))
for i in range(mib):  # 1 MiB files at the per-file cap; half Python-like text, half binary
    data = (b"def f%d():\n    return %d\n" % (i, i)) * 40000 if i % 2 else os.urandom(1 << 20)
    (root / f"f{i:05d}{'.txt' if i % 2 else '.bin'}").write_bytes(data[: 1 << 20])
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
if mode == "capture":
    from oxide.capture import capture

    snap = capture(root)
    files = len(snap.files)
else:
    import oxide

    report = oxide.index_repository(root, data_dir=root.parent / (root.name + "-data"))
    files = report.files
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(json.dumps({"mib": mib, "mode": mode, "files": files, "rss_before": round(before),
                  "peak_rss_mib": round(peak)}))
