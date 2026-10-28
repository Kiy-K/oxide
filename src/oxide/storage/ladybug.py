"""LadybugDB knowledge store (ADR-0002 rules, carried into Python by ADR-0011).

Layout of one repository's store under the data location::

    <data>/repos/<sha256(root)[:32]>/
        store.lock          exclusive OS lock: one owner per store, enforced by OXIDE
        repo.json           {"repo_id", "root"}; the random RepoId (ADR-0010)
        CURRENT             name of the published generation; replaced atomically
        generations/<g>/    sealed: graph.lbug, MANIFEST.json, source/<sha256 hex>,
                            facts.json (parse facts the next index() may reuse)

A generation is built in ``<g>.staging``, bulk-loaded with ``COPY``, read back and
compared, checkpointed, fsynced and renamed; only then does ``CURRENT`` move. A
failure leaves the previous generation current. On open, anything but the current
generation is deleted. A crash inside a same-name rebuild is undone; any other
``CURRENT`` naming a missing generation fails closed and nothing is deleted.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import sys
import urllib.request
from collections.abc import Iterable
from importlib.metadata import version
from pathlib import Path
from typing import Any

import ladybug

from oxide.capture import digest
from oxide.core.indexing import FACTS_FORMAT, components
from oxide.models import (
    Closed,
    Entity,
    FileInfo,
    IncompatibleIndex,
    InvalidPath,
    OxideError,
    Relation,
    Snapshot,
    SourceRef,
    StoreCorrupt,
)

SCHEMA_VERSION = "oxide-ladybug-py-v2"
STORAGE_FORMAT = 47
# The pool of every read view, and the floor of a load's. A load holds what it copies
# in the pool until it commits: measured ~1.6 MiB per CSV MiB (VS Code, 554k relations,
# needs 288 MiB), so a load gets twice its CSV bytes more (docs/large-repositories.md).
BUFFER_POOL = 128 << 20
LOAD_POOL_PER_BYTE = 2
FTS_VERSION = "0.21.0"
# (sys.platform, platform.machine()) -> the extension host's platform and the build's
# SHA-256: LadybugDB's v0.21.0 builds, mirrored unmodified on an OXIDE release.
FTS_BUILDS = {
    ("linux", "x86_64"): (
        "linux_amd64",
        "742f2756c2f80bcd1886b6ee0446483038cff0ecf6cf47f698bc6fe855cbaef6",
    ),
    ("darwin", "arm64"): (
        "osx_arm64",
        "248332de781a7781f612a55b5397e88f58f1f1ba2a973836ada199eab0f6bf10",
    ),
    ("darwin", "x86_64"): (
        "osx_amd64",
        "c86f427503555a3ccdc55eb66a8d82793a206aac599a6092f7d1d4eb50c21403",
    ),
    ("win32", "AMD64"): (
        "win_amd64",
        "7b6132ae6a554e3eeb3c4b0da7e2eb5c483b258b31e848a0e3d6000ac29885ee",
    ),
}
FTS_SCORER = "ladybug-fts-0.21.0-bm25;k1=1.2;b=0.75;stemmer=porter;disjunctive"
FTS_COMPONENT = "ladybug-fts-0.21.0;stemmer=porter;stopwords=default"
# (kind, other end, proven, heuristic): proven is ``resolved`` with a non-heuristic basis.
type Adjacent = list[tuple[str, str, bool, bool]]
_HEX64 = re.compile(r"[0-9a-f]{64}")
_TERMS_TEXT = re.compile(r"[a-z0-9]+( [a-z0-9]+)*")
_SEGMENTS = re.compile(r"[^.:]+(?:(?:\.|::)[^.:]+)*")


def default_data_dir() -> Path:
    """``$OXIDE_DATA_DIR``, else ``$XDG_DATA_HOME/oxide/py``, else ``~/.local/share/oxide/py``.
    Separate from the retired Rust runtime's ``oxide/v2``, which is never touched."""
    if explicit := os.environ.get("OXIDE_DATA_DIR"):
        return Path(explicit)
    xdg = os.environ.get("XDG_DATA_HOME", "")
    base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".local" / "share"
    return base / "oxide" / "py"


def fts_location() -> Path:
    if explicit := os.environ.get("OXIDE_FTS_EXTENSION"):
        return Path(explicit)
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".cache"
    return base / "oxide" / f"ladybug-extensions-{FTS_VERSION}" / "libfts.lbug_extension"


def _fts_build() -> tuple[str, str] | None:
    """The pinned extension's URL and SHA-256 for this platform, if there is one."""
    build = FTS_BUILDS.get((sys.platform, platform.machine()))
    if build is None:
        return None
    host, sha256 = build
    release = f"https://github.com/Kiy-K/oxide/releases/download/ladybug-fts-{FTS_VERSION}"
    return f"{release}/libfts-{FTS_VERSION}-{host}.lbug_extension", sha256


def _native_path_issue(text: str, variable: str) -> str | None:
    """Why LadybugDB cannot open this path, or None (docs/platforms.md). ``variable``
    names the environment variable that moves it."""
    if sys.platform == "win32" and not text.isascii():
        return f"{text!r}: on Windows LadybugDB opens only ASCII paths; set {variable}"
    return None


def fts_unavailable(path: Path) -> str | None:
    """Why the pinned FTS extension cannot be used, or None. Checked on every use:
    loading native code is a trust boundary (ADR-0002 § 3)."""
    if (build := _fts_build()) is None:
        return f"no pinned FTS extension for {sys.platform}/{platform.machine()}"
    if issue := _native_path_issue(str(path), "OXIDE_FTS_EXTENSION"):
        return issue
    try:
        data = path.read_bytes()
    except OSError:
        return f"FTS extension missing at {path}; run `oxide prepare`"
    if hashlib.sha256(data).hexdigest() != build[1]:
        return f"FTS extension at {path} does not match its pinned digest; run `oxide prepare`"
    return None


def prepare_fts(path: Path | None = None) -> Path:
    """Explicitly fetches the pinned FTS extension and verifies its digest. The only
    network access OXIDE makes; nothing calls it implicitly."""
    path = path or fts_location()
    if fts_unavailable(path) is None:
        return path
    if (build := _fts_build()) is None:
        raise OxideError(f"no pinned FTS extension for {sys.platform}/{platform.machine()}")
    if issue := _native_path_issue(str(path), "OXIDE_FTS_EXTENSION"):
        raise InvalidPath(issue)  # never download what could not be loaded
    url, sha256 = build
    path.parent.mkdir(parents=True, exist_ok=True)
    # The extension host refuses urllib's default User-Agent (HTTP 403, measured).
    request = urllib.request.Request(url, headers={"User-Agent": f"oxide/{version('oxide')}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except OSError as error:
        raise OxideError(f"could not fetch {url}: {error}") from error
    if hashlib.sha256(data).hexdigest() != sha256:
        raise OxideError(f"downloaded FTS extension does not match {sha256}")
    partial = path.with_suffix(".partial")
    partial.write_bytes(data)
    os.replace(partial, path)
    return path


def storage_components(lexical: bool) -> dict[str, str]:
    return {
        "storage-schema": SCHEMA_VERSION,
        "storage-format": str(STORAGE_FORMAT),
        "ladybug": version("ladybug"),
        "lexical": FTS_COMPONENT if lexical else "unavailable",
    }


def _rows(conn: ladybug.Connection, query: str, params: dict[str, Any] | None = None) -> list[Any]:
    result = conn.execute(query, params or {})
    assert not isinstance(result, list)
    return list(result.get_all())


def _literal(path: Path) -> str:
    """A path as a quoted Cypher literal (COPY, LOAD EXTENSION). ``\\`` and ``'`` are
    escaped; the pinned parser decodes them back byte-exactly (probed with quotes,
    backslashes, control and non-ASCII characters, docs/migration-audit)."""
    text = str(path)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InvalidPath(f"{text!r}: store and extension paths must be UTF-8") from error
    if issue := _native_path_issue(text, "OXIDE_DATA_DIR"):
        raise InvalidPath(issue)
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _field(value: object) -> str:
    """One CSV field: strings always quoted, quotes doubled; COPY reads them back
    byte-exactly (verified at the pin, docs/phase-3.md)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return '"' + str(value).replace('"', '""') + '"'


def _write_csv(path: Path, rows: Iterable[tuple[object, ...]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="") as out:
        for row in rows:
            out.write(",".join(_field(v) for v in row) + "\n")
            count += 1
    return count


def _fsync(path: Path) -> None:
    if sys.platform == "win32":
        if path.is_dir():
            # shortcut: Windows cannot open a directory to flush it, so a rename's
            # durability rests on NTFS metadata journaling; upgrade with write-through moves.
            return
        fd = os.open(path, os.O_RDWR)  # Windows flushes only handles with write access
    else:
        fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_atomic(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    _fsync(temporary)
    os.replace(temporary, path)
    _fsync(path.parent)


_SCHEMA = (
    "CREATE NODE TABLE Entity(key STRING PRIMARY KEY, category STRING, kind STRING, name STRING, "
    "signature STRING, path STRING, start_byte INT64, end_byte INT64, digest STRING, "
    "test BOOLEAN, terms STRING)",
    "CREATE NODE TABLE Unresolved(key STRING PRIMARY KEY, name STRING)",
    "CREATE REL TABLE Rel(FROM Entity TO Entity, kind STRING, resolution STRING, basis STRING, "
    "name STRING, site INT64, ev_path STRING, ev_start INT64, ev_end INT64)",
    "CREATE REL TABLE Opaque(FROM Entity TO Unresolved, kind STRING, basis STRING, name STRING, "
    "site INT64, ev_path STRING, ev_start INT64, ev_end INT64)",
)
_ENTITY = (
    "e.key, e.category, e.kind, e.name, e.signature, e.path, e.start_byte, e.end_byte, "
    "e.digest, e.test"
)


def unresolved_key(name: str) -> str:
    """One node per last ``.``/``::`` segment: ``mentions`` reads only its names' nodes."""
    return "u:" + name[max(name.rfind(".") + 1, name.rfind("::") + 2 if "::" in name else 0) :]


def _entity(row: list[Any]) -> Entity:
    key, category, kind, name, signature, path, start, end, file_digest, test = row
    source = SourceRef(path, start, end, file_digest) if start >= 0 else None
    return Entity(key, category, kind, name, signature or None, source, test)


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"expected text, got {value!r}")
    return value


def _comparable(parts: dict[str, str]) -> dict[str, str]:
    """Components that must match to read a generation; lexical availability may differ."""
    return {k: v for k, v in parts.items() if k != "lexical"}


class View:
    """A read view pinned to one sealed generation. Not thread-safe.

    Everything that can be checked without native code (manifest shape, schema,
    owner, derivation components) is checked before the database is opened. Any
    damage after that, at open or on a later read, is ``StoreCorrupt``."""

    def __init__(self, directory: Path, repo_id: str) -> None:
        try:
            manifest = json.loads((directory / "MANIFEST.json").read_bytes())
        except (OSError, ValueError) as error:
            raise StoreCorrupt(f"{directory}: unreadable manifest ({error})") from error
        if not isinstance(manifest, dict):
            raise StoreCorrupt(f"{directory}: manifest is not an object")
        if manifest.get("schema") != SCHEMA_VERSION:
            raise IncompatibleIndex(
                f"{directory}: built by store schema {manifest.get('schema')!r}; re-index"
            )
        try:
            self.snapshot = Snapshot(
                *(_text(manifest[k]) for k in ("repo_id", "snapshot_id", "derivation_id"))
            )
            self.components: dict[str, str] = dict(manifest["components"])
            self.files = {
                f[0]: FileInfo(_text(f[0]), _text(f[1]), int(f[2]), f[3], f[4], tuple(f[5]))
                for f in manifest["files"]
            }
            self.lexical_unavailable: str | None = manifest["lexical_unavailable"]
            if self.lexical_unavailable is not None:
                _text(self.lexical_unavailable)
            entities = int(manifest["rows"]["Entity"])
            self.counts = (int(manifest["entities"]), int(manifest["relations"]))
        except (KeyError, TypeError, ValueError, IndexError) as error:
            raise StoreCorrupt(f"{directory}: malformed manifest ({error!r})") from error
        if self.snapshot.repo_id != repo_id:
            raise StoreCorrupt(f"{directory}: belongs to repository {self.snapshot.repo_id}")
        if _comparable(self.components) != _comparable(components(storage_components(True))):
            raise IncompatibleIndex(f"{directory}: built by another OXIDE derivation; re-index")
        self.directory = directory
        self.manifest = manifest
        self.lexical_scorer = FTS_SCORER
        self._db: ladybug.Database | None = None
        self._conn: ladybug.Connection | None = None
        try:
            self._db = ladybug.Database(
                str(directory / "graph.lbug"),
                read_only=True,
                buffer_pool_size=BUFFER_POOL,
                max_num_threads=1,
            )
            self._conn = ladybug.Connection(self._db)
            # An empty or foreign file opens as some other database: compare what was sealed.
            stored = _rows(self._conn, "MATCH (e:Entity) RETURN count(e)")[0][0]
        except RuntimeError as error:
            self.close()
            raise StoreCorrupt(f"{directory}: unreadable database ({error})") from error
        if stored != entities:
            self.close()
            raise StoreCorrupt(f"{directory}: {stored} entities stored, {entities} sealed")
        if self.lexical_unavailable is None:
            extension = fts_location()
            self.lexical_unavailable = fts_unavailable(extension)
            if self.lexical_unavailable is None:
                try:
                    _rows(self._conn, f"LOAD EXTENSION {_literal(extension)}")
                except RuntimeError as error:
                    self.lexical_unavailable = f"FTS extension did not load: {error}"

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._db is not None:
            self._db.close()
            self._db = None

    def _query(self, query: str, params: dict[str, Any] | None = None) -> list[Any]:
        """A read; native failures on a sealed generation mean it is damaged."""
        if self._conn is None:
            raise Closed(f"{self.directory}: view is closed")
        try:
            return _rows(self._conn, query, params)
        except RuntimeError as error:
            raise StoreCorrupt(f"{self.directory}: read failed ({error})") from error

    def facts(self) -> dict[str, Any]:
        """The parse facts sealed with this generation, by dialect and file digest; empty
        when it has none of the current format or they do not match their sealed digest. A
        cache: losing it costs a re-parse, never correctness."""
        sealed = self.manifest.get("facts")
        if not isinstance(sealed, dict) or sealed.get("format") != FACTS_FORMAT:
            return {}
        try:
            data = (self.directory / "facts.json").read_bytes()
            if digest(data) != sealed.get("digest"):
                return {}
            facts = json.loads(data)
        except (OSError, ValueError):
            return {}
        return facts if isinstance(facts, dict) else {}

    def verify_sources(self) -> None:
        """Every retained source file exists and matches its digest."""
        for info in self.files.values():
            self.source(SourceRef(info.path, 0, info.size, info.digest))

    def entities(self, keys: Iterable[str]) -> dict[str, Entity]:
        wanted = sorted(set(keys))
        if not wanted:
            return {}
        query = f"MATCH (e:Entity) WHERE e.key IN $keys RETURN {_ENTITY}"
        return {row[0]: _entity(row) for row in self._query(query, {"keys": wanted})}

    def named(self, name: str, limit: int) -> tuple[list[str], bool]:
        rows = self._query(
            f"MATCH (e:Entity) WHERE e.name = $name RETURN e.key ORDER BY e.key LIMIT {limit + 1}",
            {"name": name},
        )
        return [r[0] for r in rows[:limit]], len(rows) > limit

    def innermost(self, path: str, start: int, end: int) -> str | None:
        """The smallest declaration of ``path`` whose range contains ``[start, end)``."""
        rows = self._query(
            "MATCH (e:Entity) WHERE e.category = 'symbol' AND e.path = $path "
            "AND e.start_byte <= $start AND e.end_byte >= $stop "
            "RETURN e.key ORDER BY e.end_byte - e.start_byte, e.key LIMIT 1",
            {"path": path, "start": start, "stop": end},
        )
        return rows[0][0] if rows else None

    def lexical(self, text: str, limit: int) -> tuple[list[tuple[str, float]], bool]:
        """BM25 hits in descending score, ties by key; at most ``limit``, and whether
        more matched. Entities matching nothing are absent, never scored zero."""
        if self.lexical_unavailable is not None:
            raise OxideError(f"lexical search unavailable: {self.lexical_unavailable}")
        if not text:
            return [], False
        # A parameterized QUERY_FTS_INDEX segfaults when the binding re-runs its cached
        # prepared statement (ladybug 0.21.2, measured), so the query is a literal. Only
        # OXIDE terms reach here, and they can never close the quote.
        if not _TERMS_TEXT.fullmatch(text):
            raise ValueError(f"lexical query must be OXIDE terms, got {text!r}")
        statement = (
            f"CALL QUERY_FTS_INDEX('Entity', 'lexical', '{text}', conjunctive := false, "
            "K := 1.2, B := 0.75{top}) RETURN node.key, score ORDER BY score DESC, node.key "
            "LIMIT {n}"
        )
        # `top` spares sorting every match by key (docs/phase-6/fts-footprint § 3) but cuts
        # ties arbitrarily: exact unless its last score reaches the limit-th; then rerun.
        wide = 10 * (limit + 1)
        rows = self._query(statement.format(top=f", top := {wide}", n=wide))
        if len(rows) == wide and rows[-1][1] >= rows[limit - 1][1]:
            rows = self._query(statement.format(top="", n=limit + 1))
        rows = rows[: limit + 1]
        for key, score in rows:
            if not (isinstance(score, float) and math.isfinite(score) and score > 0):
                raise StoreCorrupt(f"invalid lexical score {score!r} for {key}")
        return [(r[0], r[1]) for r in rows[:limit]], len(rows) > limit

    def adjacency(self, keys: list[str]) -> dict[str, tuple[Adjacent, Adjacent]]:
        """Resolved and ambiguous edges of every key as ``(outgoing, incoming)`` lists of
        ``(kind, other end, proven, heuristic)``: two queries for any number of keys."""
        found: dict[str, tuple[Adjacent, Adjacent]] = {key: ([], []) for key in keys}
        if not keys:
            return found
        params = {"keys": sorted(set(keys))}
        match = "MATCH (a:Entity)-[r:Rel]->(b:Entity) WHERE "
        flags = "r.resolution = 'resolved' AND r.basis <> 'heuristic', r.basis = 'heuristic'"
        out = match + f"a.key IN $keys RETURN a.key, r.kind, b.key, {flags}"
        into = match + f"b.key IN $keys RETURN b.key, r.kind, a.key, {flags}"
        for direction, query in enumerate((out, into)):
            for key, kind, other, certain, heuristic in self._query(query, params):
                found[key][direction].append((kind, other, certain, heuristic))
        return found

    def mentions(self, names: list[str], kinds: tuple[str, ...]) -> int:
        """Unresolved relations of ``kinds`` whose text is one of ``names`` or ends with
        ``.name`` or ``::name``: sites that may target them, never resolved to them."""
        if not names:
            return 0
        suffixes = sorted({f"{sep}{n}" for n in names for sep in (".", "::")})
        keys = sorted({unresolved_key(n) for n in names})
        params = {"kinds": list(kinds), "names": sorted(set(names)), "suffixes": suffixes}
        # every match shares a name's key, unless a name has odd separators: scan them all
        where = "u.key IN $keys AND " if all(_SEGMENTS.fullmatch(n) for n in names) else ""
        rows = self._query(
            f"MATCH (:Entity)-[r:Opaque]->(u:Unresolved) WHERE {where}r.kind IN $kinds AND "
            "(r.name IN $names OR any(s IN $suffixes WHERE r.name ENDS WITH s)) RETURN count(*)",
            {**params, "keys": keys},
        )
        return int(rows[0][0])

    def relations(self, key: str) -> list[Relation]:
        """Every relation touching ``key``, unresolved ones included, in derivation order."""
        columns = "r.kind, r.resolution, r.basis, r.name, r.site, r.ev_path, r.ev_start, r.ev_end"
        rows = self._query(
            "MATCH (a:Entity)-[r:Rel]->(b:Entity) WHERE a.key = $key OR b.key = $key "
            f"RETURN a.key, b.key, {columns}",
            {"key": key},
        )
        rows += self._query(
            "MATCH (a:Entity)-[r:Opaque]->(:Unresolved) WHERE a.key = $key RETURN a.key, NULL, "
            "r.kind, 'unresolved', r.basis, r.name, r.site, r.ev_path, r.ev_start, r.ev_end",
            {"key": key},
        )
        out = []
        for source, target, kind, resolution, basis, name, site, path, start, end in rows:
            evidence = SourceRef(path, start, end, self.files[path].digest) if start >= 0 else None
            relation = Relation(
                kind, source, target, resolution, basis, name or None, site, evidence
            )
            out.append(relation)
        return sorted(out, key=lambda r: (r.site, r.source, r.target or ""))

    def source(self, ref: SourceRef) -> bytes:
        """The captured bytes of ``ref.path``, verified against the pinned digest; never
        the current worktree."""
        info = self.files.get(ref.path)
        if info is None or info.digest != ref.digest:
            raise StoreCorrupt(f"{ref.path}: not in this generation at {ref.digest}")
        try:
            data = (self.directory / "source" / ref.digest.removeprefix("sha256:")).read_bytes()
        except OSError as error:
            raise StoreCorrupt(f"{ref.path}: retained source missing ({error})") from error
        if digest(data) != ref.digest:
            raise StoreCorrupt(f"{ref.path}: retained source does not match {ref.digest}")
        return data


def load_pool(size: int) -> int:
    """Buffer pool for a bulk load of ``size`` bytes; reads the module values at call time."""
    return BUFFER_POOL + LOAD_POOL_PER_BYTE * size
