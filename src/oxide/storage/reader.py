"""Read-only access to a store another process owns (see ``oxide.storage.pins``).

A ``Reader`` takes no ``store.lock``, writes nothing but its pin, and serves the latest
published generation; each ``view()`` call re-reads ``CURRENT`` so a long-lived reader
follows the owner's publications. Same failure semantics as ``Store(create=False)``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import weakref
from pathlib import Path

from oxide.models import NotIndexed, OwnershipConflict, ResourceExhausted, StoreCorrupt
from oxide.storage.ladybug import _HEX64, View, _literal
from oxide.storage.pins import pin, unpin

ATTEMPTS = 5


def _unpin_all(pins: list[tuple[int, Path]]) -> None:
    while pins:
        unpin(*pins.pop())


def read_repo_id(directory: Path, root: Path) -> str:
    """The validated ``repo.json`` identity of the store at ``directory``."""
    path = directory / "repo.json"
    try:
        meta = json.loads(path.read_bytes())
        stored, repo_id = meta["root"], meta["repo_id"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise StoreCorrupt(f"{path}: unreadable ({error!r}); remove {directory}") from error
    if stored != str(root):
        raise StoreCorrupt(f"{directory}: belongs to {stored!r}")
    if not (isinstance(repo_id, str) and repo_id):
        raise StoreCorrupt(f"{path}: invalid repo_id {repo_id!r}; remove {directory}")
    return repo_id


def _current(directory: Path) -> str | None:
    try:
        name = (directory / "CURRENT").read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise StoreCorrupt(
            f"{directory / 'CURRENT'}: unreadable ({error}); remove {directory}"
        ) from error
    if not _HEX64.fullmatch(name):
        raise StoreCorrupt(f"{directory / 'CURRENT'}: not a generation name; remove {directory}")
    return name


class Reader:
    """Duck-types the read half of ``Store``: ``view()``, ``repo_id``, ``dir``, ``close()``."""

    def __init__(self, root: Path, data: Path) -> None:
        self.root = root
        self.data = data
        self.dir = data / "repos" / hashlib.sha256(os.fsencode(root)).hexdigest()[:32]
        _literal(self.dir)
        if not ((self.dir / "repo.json").is_file() and (self.dir / "generations").is_dir()):
            if (self.dir / "CURRENT").exists():
                raise StoreCorrupt(f"{self.dir}: repo.json or generations is missing; remove it")
            raise NotIndexed(f"{root} has not been indexed")
        self.repo_id = read_repo_id(self.dir, root)
        self._view: View | None = None
        self._pins: list[tuple[int, Path]] = []  # shared with the finalizer: a leak still unpins
        self._finalizer = weakref.finalize(self, _unpin_all, self._pins)
        self.current: tuple[str, int] | None = None
        self.view()  # fail now, as Store(create=False) does

    def _identity(self) -> tuple[str, int] | None:
        """CURRENT and the inode of its directory: a same-name rebuild changes the inode."""
        name = _current(self.dir)
        if name is None:
            return None
        try:
            return name, (self.dir / "generations" / name).stat().st_ino
        except FileNotFoundError:
            return name, 0

    def view(self) -> View:
        if self._view is not None and self._identity() == self.current:
            return self._view
        self._release()
        for attempt in range(ATTEMPTS):
            if attempt:
                time.sleep(0.02 * attempt)
            name = _current(self.dir)
            if name is None:
                raise NotIndexed(f"{self.root} has not been indexed")
            try:
                held = pin(self.dir, name)
            except OSError as error:
                raise ResourceExhausted(f"{self.dir}: cannot pin a generation ({error})") from error
            self._pins.append(held)
            try:
                # The owner moves CURRENT before it sweeps, so a pin counts only if CURRENT
                # still names the generation after the pin is held.
                identity = self._identity()
                if identity is not None and identity[0] == name and identity[1]:
                    self._view = View(self.dir / "generations" / name, self.repo_id)
                    self.current = identity
                    return self._view
            except BaseException:
                self._release()
                raise
            self._release()
        raise OwnershipConflict(f"{self.root}: the index changed under every read; retry")

    def _release(self) -> None:
        if self._view is not None:
            self._view.close()
            self._view = None
        _unpin_all(self._pins)
        self.current = None

    def close(self) -> None:
        self._release()
        self._finalizer.detach()
