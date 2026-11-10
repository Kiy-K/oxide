"""The writing side of the LadybugDB store: ownership, generations and publication.

The store layout and its rules are documented in ``oxide.storage.ladybug``.
"""

import hashlib
import json
import os
import secrets
import shutil
import sys
import weakref
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import ladybug

from oxide.capture import Capture, digest
from oxide.core import blast
from oxide.core.indexing import FACTS_FORMAT, Derived, derivation_id
from oxide.core.languages.python_resolve import Edge
from oxide.core.retrieval import document
from oxide.models import (
    IncompatibleIndex,
    NativeUnavailable,
    NotIndexed,
    OwnershipConflict,
    Snapshot,
    SourceRef,
    StoreCorrupt,
)

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from oxide.storage.fts import fts_location
from oxide.storage.ladybug import (
    _HEX64,
    _LEXICAL_SCHEMA,
    _SCHEMA,
    SCHEMA_VERSION,
    STORAGE_FORMAT,
    View,
    _fsync,
    _literal,
    _rows,
    _write_atomic,
    _write_csv,
    identity,
    lexical_pool,
    load_pool,
    unresolved_key,
)


def _link(retained: Path, blob: Path, data: bytes) -> bool:
    """Hard-links the current generation's blob of ``data`` into staging, if it holds
    exactly these bytes. A damaged blob is never carried over; a missing one, or a file
    system without hard links, falls back to writing."""
    try:
        os.link(retained, blob)
    except OSError:
        return False
    try:
        if blob.read_bytes() == data:
            return True
    except OSError:
        pass
    blob.unlink()  # unlink, never overwrite: the inode is shared
    return False


class Store:
    """The single owner of one repository's store in this process.

    ``close()`` releases the view and the lock. A store dropped without it releases
    its lock when collected: the finalizer holds only the descriptor, never the store.
    """

    def __init__(self, root: Path, data: Path, create: bool = True) -> None:
        try:  # before anything is touched, or a missing library reads as damage
            ladybug.Database.get_version()
        except (ImportError, OSError, RuntimeError) as error:
            raise NativeUnavailable(
                f"LadybugDB native library failed to load ({error}); on Windows it needs "
                "the OpenSSL 3 x64 DLLs (docs/platforms.md)"
            ) from error
        self.root = root
        self.data = data
        self.dir = data / "repos" / hashlib.sha256(os.fsencode(root)).hexdigest()[:32]
        _literal(self.dir)
        if not create and not (
            (self.dir / "repo.json").is_file() and (self.dir / "generations").is_dir()
        ):
            if (self.dir / "CURRENT").exists():
                # Published once, then damaged: never "not indexed" (SPEC § failure modes).
                raise StoreCorrupt(f"{self.dir}: repo.json or generations is missing; remove it")
            # A reader never initializes a store, even a half-created one left by a crash.
            raise NotIndexed(f"{root} has not been indexed")
        self.dir.mkdir(parents=True, exist_ok=True)
        # Not inherited by child processes (PEP 446), so only this process owns it.
        lock = os.open(self.dir / "store.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if sys.platform == "win32":
                try:
                    msvcrt.locking(lock, msvcrt.LK_NBLCK, 1)
                except OSError as error:  # held by another handle
                    raise BlockingIOError(*error.args) from error
            else:
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

    def previous(self) -> View | None:
        """The current generation if it is readable under this OXIDE, else None."""
        try:
            return self.view()
        except (NotIndexed, IncompatibleIndex, StoreCorrupt):
            self._drop_view()
            return None

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
            # LadybugDB releases the GIL, so the source blobs and the lexical index are
            # written while the graph loads (docs/fts-footprint.md § 4).
            lexical = None
            with ThreadPoolExecutor(2) as pool:
                retaining = pool.submit(self._retain, staging, capture)
                if lexical_unavailable is None:
                    lexical, count = self._documents(staging, derived)
                    old = self.previous()
                    same = old is not None and old.manifest.get("lexical") == lexical
                    reuse = old.directory if old is not None and same else None
                    indexing = pool.submit(self._lexical, staging, lexical, count, reuse)
                snapshot = Snapshot(self.repo_id, capture.snapshot_id, derivation)
                rows = self._load(staging, derived, snapshot)
                retaining.result()
                if lexical is not None:
                    indexing.result()
            facts = json.dumps(derived.facts, separators=(",", ":")).encode()
            (staging / "facts.json").write_bytes(facts)
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
                "lexical": lexical,
                "facts": {"format": FACTS_FORMAT, "digest": digest(facts)},
            }
            self._seal(staging, manifest)
            target = self.generations / name
            if target.exists():
                # The unreadable generation this rebuild replaces. A crash between the two
                # renames is undone on the next open (_recover); a failure, just below.
                # Close our own view of it first: Windows can't rename a directory with
                # open files.
                self._drop_view()
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

    def _retain(self, staging: Path, capture: Capture) -> None:
        """Makes the captured bytes durable in the staging's ``source`` directory."""
        source = staging / "source"
        source.mkdir()
        retained = self.generations / self.current / "source" if self.current else None
        for f in capture.files:
            name = f.digest.removeprefix("sha256:")
            blob = source / name
            if blob.exists() or (retained and _link(retained / name, blob, f.data)):
                continue  # a linked blob was made durable when its generation was sealed
            blob.write_bytes(f.data)
            _fsync(blob)
        _fsync(source)

    def _documents(self, staging: Path, derived: Derived) -> tuple[str, int]:
        """Writes the lexical documents; their digest identifies the lexical index."""
        csv = staging / "Doc.csv"
        count = _write_csv(csv, ((e.key, document(e)) for e in derived.entities))
        return digest(csv.read_bytes()), count

    def _lexical(self, staging: Path, documents: str, count: int, reuse: Path | None) -> None:
        """Builds ``lexical.lbug`` from ``Doc.csv``, or links ``reuse``'s, a readable
        generation with the same documents: the index is a function of them alone."""
        csv, target = staging / "Doc.csv", staging / "lexical.lbug"
        if reuse is not None:
            try:
                os.link(reuse / "lexical.lbug", target)
                csv.unlink()
                return
            except OSError:
                pass  # no hard links here: build it
        # One thread: on two, FTS BM25 scores drift by one ULP in ~10% of builds
        # (indexing-throughput § 9 at 013e791). Only the final CHECKPOINT writes pages:
        # an auto-checkpoint inside CREATE_FTS_INDEX leaves dead pages (docs/fts-footprint.md).
        db = ladybug.Database(
            str(target),
            buffer_pool_size=lexical_pool(csv.stat().st_size),
            max_num_threads=1,
            auto_checkpoint=False,
        )
        conn = ladybug.Connection(db)
        try:
            for statement in _LEXICAL_SCHEMA:
                _rows(conn, statement)
            options = "header=false, quote='\"', escape='\"', delim=',', parallel=false"
            _rows(conn, f"COPY Doc FROM {_literal(csv)} ({options})")
            csv.unlink()
            read = _rows(conn, "MATCH (d:Doc) RETURN count(d)")[0][0]
            if read != count:
                raise StoreCorrupt(f"read back {read} documents, wrote {count}")
            _rows(conn, "CREATE (:Identity {key: $key})", {"key": documents})
            _rows(conn, f"LOAD EXTENSION {_literal(fts_location())}")
            _rows(conn, "CALL CREATE_FTS_INDEX('Doc', 'lexical', ['terms'], stemmer := 'porter')")
            _rows(conn, "CHECKPOINT")
        finally:
            conn.close()
            db.close()

    def _seal(self, staging: Path, manifest: dict[str, Any]) -> None:
        """Writes the manifest, then makes the staging durable."""
        (staging / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        for path in staging.iterdir():
            # A linked lexical index was made durable when its generation was sealed (as
            # with blobs); flushing it would open a file the still-open previous view holds,
            # which Windows may refuse.
            if path.is_file() and path.stat().st_nlink == 1:
                _fsync(path)
        _fsync(staging)

    def _load(self, staging: Path, derived: Derived, snapshot: Snapshot) -> dict[str, int]:
        # Rows stream to CSV instead of lists: those lists were the largest Python share
        # of the publish peak on VS Code while the lexical index builds alongside.
        none = SourceRef("", -1, -1, "")

        def entities() -> Iterator[tuple[object, ...]]:
            for e, root in zip(derived.entities, roots, strict=True):
                src = e.source or none
                row = (e.key, e.category, e.kind, e.name, e.signature or "", src.path, src.start)
                yield (*row, src.end, src.digest, e.test, *blast.blast_columns(root))

        unresolved: dict[str, tuple[object, ...]] = {}

        def tail(site: int, edge: Edge) -> tuple[object, ...]:
            ev = edge.evidence or none
            return (edge.name or "", site, ev.path, ev.start, ev.end)

        def rels() -> Iterator[tuple[object, ...]]:
            for site, edge in enumerate(derived.edges):
                for target in edge.targets:
                    yield (
                        edge.source,
                        target,
                        edge.kind,
                        edge.resolution,
                        edge.basis,
                        *tail(site, edge),
                    )

        def opaque() -> Iterator[tuple[object, ...]]:
            for site, edge in enumerate(derived.edges):
                if edge.resolution == "unresolved":
                    node = unresolved_key(edge.name or "")
                    unresolved.setdefault(node, (node, node[2:]))
                    yield (edge.source, node, edge.kind, edge.basis, *tail(site, edge))

        roots = blast.compute(derived.entities, derived.edges)
        written = {
            table: _write_csv(staging / f"{table}.csv", rows)
            for table, rows in (
                ("Entity", entities()),
                ("Rel", rels()),
                ("Opaque", opaque()),
            )
        }
        # Filled while Opaque was written; COPY still loads nodes before relationships.
        written["Unresolved"] = _write_csv(staging / "Unresolved.csv", unresolved.values())
        tables = ("Entity", "Unresolved", "Rel", "Opaque")
        size = sum((staging / f"{table}.csv").stat().st_size for table in tables)
        pool = load_pool(size)
        # The staging load alone gets a second thread for COPY (rows unchanged); read views
        # keep one.
        db = ladybug.Database(
            str(staging / "graph.lbug"),
            buffer_pool_size=pool,
            max_num_threads=2,
            auto_checkpoint=False,
        )
        conn = ladybug.Connection(db)
        try:
            if db.get_storage_version() != STORAGE_FORMAT:
                raise IncompatibleIndex(f"ladybug storage format is not {STORAGE_FORMAT}")
            for statement in _SCHEMA:
                _rows(conn, statement)
            _rows(conn, "BEGIN TRANSACTION")
            for table in tables:
                csv = staging / f"{table}.csv"
                if written[table]:
                    options = "header=false, quote='\"', escape='\"', delim=',', parallel=false"
                    _rows(conn, f"COPY {table} FROM {_literal(csv)} ({options})")
                csv.unlink()
            _rows(conn, "CREATE (:Identity {key: $key})", {"key": identity(snapshot)})
            _rows(conn, "COMMIT")
            counted = {
                "Entity": "MATCH (e:Entity) RETURN count(e)",
                "Unresolved": "MATCH (u:Unresolved) RETURN count(u)",
                "Rel": "MATCH ()-[r:Rel]->() RETURN count(r)",
                "Opaque": "MATCH ()-[r:Opaque]->() RETURN count(r)",
            }
            read = {table: _rows(conn, query)[0][0] for table, query in counted.items()}
            if read != written:
                # COPY can drop rows whose endpoints are missing; never publish that.
                raise StoreCorrupt(f"read back {read}, wrote {written}")
            _rows(conn, "CHECKPOINT")
        finally:
            conn.close()
            db.close()
        return read
