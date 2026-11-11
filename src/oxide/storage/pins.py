"""Reader pins: how a second process reads a store without taking its ownership.

The owner (``store.lock``) is still the only writer. A reader holds one *pin* per
generation it has open: a file ``<store>/pins/<generation>.<token>`` whose exclusive OS
lock lasts as long as the reader's descriptor. The owner never deletes a generation
while a live pin names it (``sweep``); a crashed reader's pin is unlocked, so it is
swept as stale. A pin is only trusted once ``CURRENT`` is re-read after taking it
(``Reader``), because the owner moves ``CURRENT`` before it sweeps.
"""

from __future__ import annotations

import errno
import os
import secrets
import shutil
import sys
from pathlib import Path

from oxide.storage.ladybug import _HEX64

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


def try_lock(fd: int) -> bool:
    """Takes an exclusive, non-blocking OS lock on ``fd``; False when another handle holds it."""
    try:
        if sys.platform == "win32":
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    except OSError as error:
        # msvcrt reports a held lock as EACCES/EDEADLOCK; anything else is a real failure.
        if sys.platform != "win32" or error.errno not in (errno.EACCES, errno.EDEADLOCK):
            raise
        return False
    return True


def pin(store_dir: Path, generation: str) -> tuple[int, Path]:
    """A held pin on ``generation``: the locked descriptor and its path, for ``unpin``."""
    directory = store_dir / "pins"
    directory.mkdir(exist_ok=True)
    for _ in range(3):
        path = directory / f"{generation}.{secrets.token_hex(8)}"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        # The owner's stale-pin scan may have locked it, or unlinked it after unlocking and
        # before our lock took: a pin whose file is gone protects nothing. Take a new name.
        if try_lock(fd) and os.fstat(fd).st_nlink:
            return fd, path
        os.close(fd)
    raise OSError(f"{directory}: could not take a pin")


def unpin(fd: int, path: Path) -> None:
    os.close(fd)
    path.unlink(missing_ok=True)


def pinned(store_dir: Path) -> set[str]:
    """Generations with a live pin. Pins whose holder is gone are removed."""
    live: set[str] = set()
    directory = store_dir / "pins"
    if not directory.is_dir():
        return live
    for entry in directory.iterdir():
        name = entry.name.partition(".")[0]
        try:
            fd = os.open(entry, os.O_RDWR)
        except FileNotFoundError:
            continue
        except OSError:  # cannot tell, so assume a reader holds it: fail closed
            live.add(name)
            continue
        try:
            if not try_lock(fd):
                live.add(name)
                continue
            # Unlink while still holding the lock, so a reader cannot lock this file in
            # between and be left pinning an unlinked name that no scan can see.
            try:
                entry.unlink(missing_ok=True)
            except OSError:  # Windows refuses to unlink a file another handle just locked
                live.add(name)
        finally:
            os.close(fd)
    return live


def sweep(store_dir: Path, generations: Path, current: str | None) -> None:
    """Deletes every generation entry but ``current`` and the pinned ones. A pinned
    generation's ``.replaced`` twin (a same-name rebuild) is what its reader may still be
    reading, so it stays too; staging leftovers are never pinned."""
    live = pinned(store_dir)
    for entry in generations.iterdir():
        base, _, suffix = entry.name.partition(".")
        keeps = entry.name if suffix in ("", "replaced") else ""
        if entry.name == current or (_HEX64.fullmatch(base) and base in live and keeps):
            continue
        shutil.rmtree(entry, ignore_errors=True)
