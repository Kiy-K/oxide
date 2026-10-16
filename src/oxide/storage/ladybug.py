"""LadybugDB knowledge store (ADR-0002 rules, carried into Python by ADR-0011).

Layout of one repository's store under the data location::

    <data>/repos/<sha256(root)[:32]>/
        store.lock          exclusive OS lock: one owner per store, enforced by OXIDE
        repo.json           {"repo_id", "root"}; the random RepoId (ADR-0010)
        CURRENT             name of the published generation; replaced atomically
        generations/<g>/    sealed: graph.lbug, MANIFEST.json, source/<sha256 hex>

A generation is built in ``<g>.staging``, bulk-loaded with ``COPY``, read back and
compared, checkpointed, fsynced and renamed; only then does ``CURRENT`` move. A
failure leaves the previous generation current. On open, anything but the current
generation is deleted. A crash inside a same-name rebuild is undone; any other
``CURRENT`` naming a missing generation fails closed and nothing is deleted.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import platform
import re
import secrets
import shutil
import sys
import urllib.request
import weakref
from collections.abc import Iterable
from importlib.metadata import version
from pathlib import Path
from typing import Any

import ladybug

from oxide.capture import Capture, digest
from oxide.core.indexing import Derived, components, derivation_id
from oxide.core.retrieval import document
from oxide.models import (
    Closed,
    Entity,
    FileInfo,
    IncompatibleIndex,
    InvalidPath,
    NotIndexed,
    OwnershipConflict,
    OxideError,
    Relation,
    Snapshot,
    SourceRef,
    StoreCorrupt,
)

SCHEMA_VERSION = "oxide-ladybug-py-v1"
STORAGE_FORMAT = 47
# shortcut: one fixed pool; very large repositories may need more, measure first.
BUFFER_POOL = 128 << 20
FTS_VERSION = "0.21.0"
FTS_SHA256 = "742f2756c2f80bcd1886b6ee0446483038cff0ecf6cf47f698bc6fe855cbaef6"
FTS_URL = f"https://extension.ladybugdb.com/v{FTS_VERSION}/linux_amd64/fts/libfts.lbug_extension"
FTS_SCORER = "ladybug-fts-0.21.0-bm25;k1=1.2;b=0.75;stemmer=porter;disjunctive"
FTS_COMPONENT = "ladybug-fts-0.21.0;stemmer=porter;stopwords=default"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_TERMS_TEXT = re.compile(r"[a-z0-9]+( [a-z0-9]+)*")


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


def _supported_platform() -> bool:
    return sys.platform == "linux" and platform.machine() in ("x86_64", "AMD64")


def fts_unavailable(path: Path) -> str | None:
    """Why the pinned FTS extension cannot be used, or None. Checked on every use:
    loading native code is a trust boundary (ADR-0002 § 3)."""
    if not _supported_platform():
        return f"no pinned FTS extension for {sys.platform}/{platform.machine()}"
    try:
        data = path.read_bytes()
    except OSError:
        return f"FTS extension missing at {path}; run `oxide prepare`"
    if hashlib.sha256(data).hexdigest() != FTS_SHA256:
        return f"FTS extension at {path} does not match its pinned digest; run `oxide prepare`"
    return None


def prepare_fts(path: Path | None = None) -> Path:
    """Explicitly fetches the pinned FTS extension and verifies its digest. The only
    network access OXIDE makes; nothing calls it implicitly."""
    path = path or fts_location()
    if fts_unavailable(path) is None:
        return path
    if not _supported_platform():
        raise OxideError(f"no pinned FTS extension for {sys.platform}/{platform.machine()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    # The extension host refuses urllib's default User-Agent (HTTP 403, measured).
    request = urllib.request.Request(FTS_URL, headers={"User-Agent": f"oxide/{version('oxide')}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except OSError as error:
        raise OxideError(f"could not fetch {FTS_URL}: {error}") from error
    if hashlib.sha256(data).hexdigest() != FTS_SHA256:
        raise OxideError(f"downloaded FTS extension does not match {FTS_SHA256}")
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
        rows = self._query(
            f"CALL QUERY_FTS_INDEX('Entity', 'lexical', '{text}', conjunctive := false, "
            "K := 1.2, B := 0.75) RETURN node.key, score ORDER BY score DESC, node.key "
            f"LIMIT {limit + 1}",
        )
        for key, score in rows:
            if not (isinstance(score, float) and math.isfinite(score) and score > 0):
                raise StoreCorrupt(f"invalid lexical score {score!r} for {key}")
        return [(r[0], r[1]) for r in rows[:limit]], len(rows) > limit

    def adjacency(
        self, keys: list[str]
    ) -> dict[str, tuple[list[tuple[str, str]], list[tuple[str, str]]]]:
        """Resolved and ambiguous edges of every key as ``(outgoing, incoming)`` lists of
        ``(kind, other end)``: two queries for any number of keys."""
        found: dict[str, tuple[list[tuple[str, str]], list[tuple[str, str]]]] = {
            key: ([], []) for key in keys
        }
        if not keys:
            return found
        params = {"keys": sorted(set(keys))}
        match = "MATCH (a:Entity)-[r:Rel]->(b:Entity) WHERE "
        out = match + "a.key IN $keys RETURN a.key, r.kind, b.key"
        into = match + "b.key IN $keys RETURN b.key, r.kind, a.key"
        for direction, query in enumerate((out, into)):
            for key, kind, other in self._query(query, params):
                found[key][direction].append((kind, other))
        return found

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


class Store:
    """The single owner of one repository's store in this process.

    ``close()`` releases the view and the lock. A store dropped without it releases
    its lock when collected: the finalizer holds only the descriptor, never the store.
    """

    def __init__(self, root: Path, data: Path, create: bool = True) -> None:
        self.root = root
        self.data = data
        self.dir = data / "repos" / hashlib.sha256(os.fsencode(root)).hexdigest()[:32]
        _literal(self.dir)
        if not create and not self.dir.is_dir():
            # A reader never leaves an empty store behind for a repository never indexed.
            raise NotIndexed(f"{root} has not been indexed")
        self.dir.mkdir(parents=True, exist_ok=True)
        lock = os.open(self.dir / "store.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(lock)
            raise OwnershipConflict(f"{root}: its store is open in another OXIDE") from error
        self._release = weakref.finalize(self, os.close, lock)
        self._view: View | None = None
        try:
            self.repo_id = self._repo_id()
            self.generations = self.dir / "generations"
            self.generations.mkdir(exist_ok=True)
            self.current = self._recover()
        except BaseException:
            self._release()
            raise

    def _repo_id(self) -> str:
        path = self.dir / "repo.json"
        if not path.exists():
            if (self.dir / "CURRENT").exists():
                # Generations without their identity: never re-keyed or deleted silently.
                raise StoreCorrupt(f"{path} is missing; remove {self.dir} to rebuild")
            repo_id = secrets.token_hex(16)
            _write_atomic(path, json.dumps({"repo_id": repo_id, "root": str(self.root)}).encode())
            return repo_id
        try:
            meta = json.loads(path.read_bytes())
            root, repo_id = meta["root"], meta["repo_id"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise StoreCorrupt(f"{path}: unreadable ({error!r}); remove {self.dir}") from error
        if root != str(self.root):
            raise StoreCorrupt(f"{self.dir}: belongs to {root!r}")
        if not (isinstance(repo_id, str) and repo_id):
            raise StoreCorrupt(f"{path}: invalid repo_id {repo_id!r}; remove {self.dir}")
        return repo_id

    def _restore(self, name: str) -> None:
        """Undoes publish's first rename when its second one never happened."""
        target, replaced = self.generations / name, self.generations / f"{name}.replaced"
        if not target.exists() and replaced.is_dir():
            os.rename(replaced, target)
            _fsync(self.generations)

    def _recover(self) -> str | None:
        pointer = self.dir / "CURRENT"
        if not pointer.exists():
            current = None
        else:
            try:
                current = pointer.read_text(encoding="ascii").strip()
            except (OSError, ValueError) as error:
                raise StoreCorrupt(f"{pointer}: unreadable ({error}); remove {self.dir}") from error
            if not _HEX64.fullmatch(current):
                raise StoreCorrupt(f"{pointer}: not a generation name; remove {self.dir}")
            # A crash between publish's two renames: the replaced generation goes back
            # in place. It was unreadable or stale, so the next index() rebuilds it.
            self._restore(current)
            if not (self.generations / current / "MANIFEST.json").is_file():
                # Ambiguous: nothing is deleted, nothing is served.
                raise StoreCorrupt(f"{pointer} names a missing generation; remove {self.dir}")
        # No view can be pinned before this owner existed: everything else is garbage.
        for entry in self.generations.iterdir():
            if entry.name != current:
                shutil.rmtree(entry, ignore_errors=True)
        return current

    def view(self) -> View:
        if self.current is None:
            raise NotIndexed(f"{self.root} has not been indexed")
        if self._view is None:
            self._view = View(self.generations / self.current, self.repo_id)
        return self._view

    def _drop_view(self) -> None:
        if self._view is not None:
            self._view.close()
            self._view = None

    def close(self) -> None:
        self._drop_view()
        self._release()

    def _name(self, snapshot_id: str, components: dict[str, str]) -> tuple[str, str]:
        derivation = derivation_id(components)
        identity = json.dumps([self.repo_id, snapshot_id, derivation]).encode()
        return hashlib.sha256(identity).hexdigest(), derivation

    def is_current(self, snapshot_id: str, components: dict[str, str]) -> bool:
        """Whether this capture and derivation are already published, readable and backed
        by every retained source file."""
        if self._name(snapshot_id, components)[0] != self.current:
            return False
        try:
            self.view().verify_sources()
            return True
        except (IncompatibleIndex, StoreCorrupt):
            self._drop_view()
            return False  # unreadable under this OXIDE: rebuilt, never migrated

    def publish(
        self,
        capture: Capture,
        derived: Derived,
        components: dict[str, str],
        lexical_unavailable: str | None,
    ) -> None:
        """Builds, seals and atomically publishes the generation."""
        name, derivation = self._name(capture.snapshot_id, components)
        staging = self.generations / f"{name}.staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        try:
            rows = self._load(staging, derived, lexical_unavailable is None)
            manifest = {
                "schema": SCHEMA_VERSION,
                "repo_id": self.repo_id,
                "snapshot_id": capture.snapshot_id,
                "derivation_id": derivation,
                "components": components,
                "lexical_unavailable": lexical_unavailable,
                "files": [
                    [f.path, f.digest, f.size, f.language, f.coverage, list(f.diagnostics)]
                    for f in derived.files
                ],
                "skipped": [[s.path, s.reason] for s in capture.skipped],
                "case_collisions": [list(group) for group in capture.case_collisions],
                "entities": len(derived.entities),
                "relations": len(derived.edges),
                "rows": rows,
            }
            self._seal(staging, capture, manifest)
            target = self.generations / name
            if target.exists():
                # The unreadable generation this rebuild replaces. A crash between the two
                # renames is undone on the next open (_recover); a failure, just below.
                os.rename(target, self.generations / f"{name}.replaced")
            os.rename(staging, target)
            _fsync(self.generations)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            self._restore(name)
            raise
        _write_atomic(self.dir / "CURRENT", name.encode())
        previous, self.current = self.current, name
        self._drop_view()
        for stale in {previous, f"{name}.replaced"} - {name, None}:
            shutil.rmtree(self.generations / str(stale), ignore_errors=True)

    def _seal(self, staging: Path, capture: Capture, manifest: dict[str, Any]) -> None:
        """Retains the captured bytes and the manifest, then makes the staging durable."""
        source = staging / "source"
        source.mkdir()
        for f in capture.files:
            blob = source / f.digest.removeprefix("sha256:")
            if not blob.exists():
                blob.write_bytes(f.data)
        (staging / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False))
        for path in [*source.iterdir(), *staging.iterdir()]:
            if path.is_file():
                _fsync(path)
        _fsync(source)
        _fsync(staging)

    def _load(self, staging: Path, derived: Derived, lexical: bool) -> dict[str, int]:
        entities: list[tuple[object, ...]] = []
        for e in derived.entities:
            src = e.source or SourceRef("", -1, -1, "")
            row = (e.key, e.category, e.kind, e.name, e.signature or "", src.path, src.start)
            entities.append((*row, src.end, src.digest, e.test, document(e)))
        rels: list[tuple[object, ...]] = []
        opaque: list[tuple[object, ...]] = []
        unresolved: list[tuple[object, ...]] = []
        for site, edge in enumerate(derived.edges):
            ev = edge.evidence or SourceRef("", -1, -1, "")
            tail = (edge.name or "", site, ev.path, ev.start, ev.end)
            if edge.resolution == "unresolved":
                unresolved.append((f"u:{site}", edge.name or ""))
                opaque.append((edge.source, f"u:{site}", edge.kind, edge.basis, *tail))
            for target in edge.targets:
                rels.append((edge.source, target, edge.kind, edge.resolution, edge.basis, *tail))
        tables = {"Entity": entities, "Unresolved": unresolved, "Rel": rels, "Opaque": opaque}
        db = ladybug.Database(
            str(staging / "graph.lbug"), buffer_pool_size=BUFFER_POOL, max_num_threads=1
        )
        conn = ladybug.Connection(db)
        try:
            if db.get_storage_version() != STORAGE_FORMAT:
                raise IncompatibleIndex(f"ladybug storage format is not {STORAGE_FORMAT}")
            for statement in _SCHEMA:
                _rows(conn, statement)
            _rows(conn, "BEGIN TRANSACTION")
            for table, rows in tables.items():
                csv = staging / f"{table}.csv"
                if _write_csv(csv, rows):
                    options = "header=false, quote='\"', escape='\"', delim=',', parallel=false"
                    _rows(conn, f"COPY {table} FROM {_literal(csv)} ({options})")
                csv.unlink()
            _rows(conn, "COMMIT")
            counted = {
                "Entity": "MATCH (e:Entity) RETURN count(e)",
                "Unresolved": "MATCH (u:Unresolved) RETURN count(u)",
                "Rel": "MATCH ()-[r:Rel]->() RETURN count(r)",
                "Opaque": "MATCH ()-[r:Opaque]->() RETURN count(r)",
            }
            read = {table: _rows(conn, query)[0][0] for table, query in counted.items()}
            written = {table: len(rows) for table, rows in tables.items()}
            if read != written:
                # COPY can drop rows whose endpoints are missing; never publish that.
                raise StoreCorrupt(f"read back {read}, wrote {written}")
            if lexical:
                _rows(conn, f"LOAD EXTENSION {_literal(fts_location())}")
                _rows(
                    conn,
                    "CALL CREATE_FTS_INDEX('Entity', 'lexical', ['terms'], stemmer := 'porter')",
                )
            _rows(conn, "CHECKPOINT")
        finally:
            conn.close()
            db.close()
        return read
