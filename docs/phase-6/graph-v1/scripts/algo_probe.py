"""Can LadybugDB's native ``algo`` extension run at the pinned ladybug==0.21.0?

Downloads this platform's build into DIR, refuses it unless its SHA-256 matches the pin
below (loading native code is a trust boundary, ADR-0002 § 3, as for FTS), then tries
``LOAD EXTENSION`` and the algorithms on a tiny projected graph. Research only: OXIDE
does not ship or load ``algo``.

    uv run python docs/phase-6/graph-v1/scripts/algo_probe.py DIR
"""

from __future__ import annotations

import hashlib
import platform
import sys
import urllib.request
from pathlib import Path

import ladybug

VERSION = "0.21.0"
# (sys.platform, platform.machine()) -> extension host platform, SHA-256 (fetched 2026-10-23).
ALGO_BUILDS = {
    ("linux", "x86_64"): (
        "linux_amd64",
        "6bc1e3dc5f3ec0796aafbc85417d79b82f093d65617d15731cab744e81de1cca",
    ),
    ("darwin", "arm64"): (
        "osx_arm64",
        "77b891d7bbf0c814be6c496c31caafbde6665a988e9889d6d44b4cdca458cca7",
    ),
    ("darwin", "x86_64"): (
        "osx_amd64",
        "e4deec75403ad033abc0092ba749b3a68b3a32bf757743115f484bd5b3bfcb4c",
    ),
    ("win32", "AMD64"): (
        "win_amd64",
        "270777b160b880496350ed275ade8165f77ca67dc0cb5e18e8197041e0793c3f",
    ),
}
QUERIES = {
    "scc": "CALL strongly_connected_components('G') RETURN node.k, group_id",
    "page_rank": "CALL page_rank('G') RETURN node.k, rank",
    "louvain": "CALL louvain('G') RETURN node.k, louvain_id",
}


def main(directory: Path) -> None:
    host, sha256 = ALGO_BUILDS[(sys.platform, platform.machine())]
    url = f"https://extension.ladybugdb.com/v{VERSION}/{host}/algo/libalgo.lbug_extension"
    request = urllib.request.Request(url, headers={"User-Agent": "oxide-research"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise SystemExit(f"{url}: digest does not match the pin; not loaded")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "libalgo.lbug_extension"
    path.write_bytes(data)
    connection = ladybug.Connection(ladybug.Database(str(directory / "probe.lbug")))
    try:
        connection.execute(f"LOAD EXTENSION '{path}'")
    except RuntimeError as error:
        print(f"load: FAILED ({error})")
        return
    for query in (
        "CREATE NODE TABLE N(k STRING PRIMARY KEY)",
        "CREATE REL TABLE R(FROM N TO N)",
        "CREATE (:N {k: 'a'}), (:N {k: 'b'})",
        "MATCH (a:N {k: 'a'}), (b:N {k: 'b'}) CREATE (a)-[:R]->(b), (b)-[:R]->(a)",
        "CALL PROJECT_GRAPH('G', ['N'], ['R'])",
    ):
        connection.execute(query)
    for name, query in QUERIES.items():
        result = connection.execute(query)
        assert not isinstance(result, list)
        print(f"{name}: {sorted(result.get_all())}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
