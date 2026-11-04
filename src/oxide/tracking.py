"""Local event invalidation and bounded reconciliation; no event history or telemetry."""

from __future__ import annotations

import threading
import time
import weakref
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Concatenate

from watchfiles import Change, watch

from oxide.capture import EXCLUDED

if TYPE_CHECKING:
    from oxide.api import Repository

# Healthy watcher: a rare safety-net walk. Failed watcher: poll often, since events are gone.
RECONCILE_SECONDS = 300.0
POLL_SECONDS = 30.0


def serialized[**P, R](
    method: Callable[Concatenate[Repository, P], R],
) -> Callable[Concatenate[Repository, P], R]:
    @wraps(method)
    def call(self: Repository, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._lock:
            return method(self, *args, **kwargs)

    return call


class Tracking:
    def __init__(self, owner: Repository, data: Path) -> None:
        self.root = owner.root
        data = data.resolve()
        self.excluded = tuple(
            path
            for path in (data, data / "repos")
            if path != self.root and path.is_relative_to(self.root)
        )
        self.owner = weakref.ref(owner)
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.dirty = threading.Event()
        self.last_full = time.monotonic()
        self.error: type[Exception] | None = None
        self.failed = False
        self.thread = threading.Thread(target=self._run, name="oxide-freshness", daemon=True)
        self.thread.start()

    def accept(self, change: Change, path: str) -> bool:
        absolute = Path(path)
        try:
            relative = absolute.relative_to(self.root)
        except ValueError:
            return False
        # Do not use watchfiles' default filter: it drops source directories and .gitignore.
        return not (
            set(relative.parts) & EXCLUDED
            or any(absolute.is_relative_to(path) for path in self.excluded)
        )

    def close(self) -> None:
        self.stop.set()
        if threading.current_thread() is not self.thread:
            self.thread.join()

    def _tick(self, changes: set[tuple[Change, str]]) -> None:
        first = not self.ready.is_set()
        try:
            self._reconcile(changes, first)
        finally:
            # Ready means the watcher is live and the post-start reconcile has finished, so
            # callers never race that background update.
            self.ready.set()

    def _reconcile(self, changes: set[tuple[Change, str]], first: bool) -> None:
        if first or changes:
            self.dirty.set()
        interval = min(RECONCILE_SECONDS, POLL_SECONDS) if self.failed else RECONCILE_SECONDS
        full = time.monotonic() - self.last_full >= interval
        if not (full or self.dirty.is_set()) or self.stop.is_set():
            return
        owner = self.owner()
        if owner is not None:
            try:
                paths = tuple(Path(path).relative_to(self.root).as_posix() for _, path in changes)
                owner._update(full, paths)
            except Exception as error:
                # Tracebacks retain the owner; keep only the failure kind until a retry succeeds.
                self.error = type(error)
                self.dirty.set()
            finally:
                del owner

    def _run(self) -> None:
        try:
            for changes in watch(
                self.root,
                watch_filter=self.accept,
                debounce=1000,
                step=200,
                stop_event=self.stop,
                rust_timeout=100,
                yield_on_timeout=True,
                debug=False,
                raise_interrupt=False,
                ignore_permission_denied=True,
            ):
                self._tick(changes)
                if self.error is not None and self.stop.wait(1):
                    return
        except (OSError, RuntimeError):
            # Overflow, watch limits and unavailable backends degrade to reconciliation.
            self.failed = True
            self.dirty.set()
            self.ready.set()
        while not self.stop.wait(1):
            self._tick(set())
