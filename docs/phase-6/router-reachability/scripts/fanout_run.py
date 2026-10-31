"""fanout_run.py FANOUT OUT [--only PREFIX]: the harness with routing.MAX_FANOUT replaced (oracle ablation)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests/python"))
from oxide.core import routing

routing.MAX_FANOUT = int(sys.argv[1])
import contextbench_ts as ts

sys.exit(ts.main(["run", "--out", sys.argv[2], *sys.argv[3:]]))
