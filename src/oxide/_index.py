"""Capture, derive and publish; shared by explicit and automatic indexing."""

from __future__ import annotations

import gc
import time
from collections.abc import Callable
from pathlib import Path

from oxide.capture import CaptureCache, capture
from oxide.core.indexing import components, derive, source_dialect, validate
from oxide.models import IndexReport, Snapshot
from oxide.storage.fts import fts_location, fts_unavailable
from oxide.storage.ladybug import storage_components
from oxide.storage.store import Store


def index(
    root: Path, store: Store, cache: CaptureCache, progress: Callable[[str], None] | None = None
) -> IndexReport:
    started = time.perf_counter()
    report = progress or (lambda line: None)
    report(f"capturing {root}")
    # The data location is never source, wherever it sits (compared by identity).
    snapshot = capture(root, exclude=(store.data, store.data / "repos"), cache=cache)
    lexical_unavailable = fts_unavailable(fts_location())
    parts = components(storage_components(lexical_unavailable is None))
    built = not store.is_current(snapshot.snapshot_id, parts)
    previous: Snapshot | None = None
    changes: tuple[tuple[str, ...], ...] = ((), (), ())
    parsed = reused = 0
    if built:
        old = store.previous()
        before = {f.path: f.digest for f in old.files.values()} if old else {}
        after = {f.path: f.digest for f in snapshot.files}
        changes = (
            tuple(p for p in after if p not in before),
            tuple(p for p in after if p in before and before[p] != after[p]),
            tuple(sorted(p for p in before if p not in after)),
        )
        previous = old.snapshot if old else None
        cached = old.facts() if old else {}
        sources = [
            (lang, f.digest)
            for f in snapshot.files
            if (lang := source_dialect(f.path, f.data)) is not None
        ]
        hits = sum(isinstance(s := cached.get(lang), dict) and d in s for lang, d in sources)
        report(
            f"deriving {len(after)} files: parsing {len(sources) - hits} source files, "
            f"reusing {hits}"
        )
        # Cyclic GC re-scans millions of acyclic build objects (~8%, indexing-throughput
        # at 013e791). Restore the caller's process-global state on every exit.
        enabled = gc.isenabled()
        gc.disable()
        try:
            derived = derive(snapshot, store.repo_id, cached)
            reused, parsed = derived.reused, len(sources) - derived.reused
            if reused < hits:
                report(f"{hits - reused} cached parse facts were unusable and parsed again")
            validate(derived)
            report(f"publishing {len(derived.entities)} entities, {len(derived.edges)} relations")
            store.publish(snapshot, derived, parts, lexical_unavailable)
        finally:
            if enabled:
                gc.enable()
    view = store.view()
    return IndexReport(
        snapshot=view.snapshot,
        status="built" if built else "unchanged",
        files=len(view.files),
        entities=view.counts[0],
        relations=view.counts[1],
        partial=tuple(f.path for f in view.files.values() if f.coverage == "partial"),
        unsupported=sum(f.coverage == "unsupported" for f in view.files.values()),
        skipped=snapshot.skipped,
        case_collisions=snapshot.case_collisions,
        lexical=view.lexical_unavailable or "available",
        seconds=time.perf_counter() - started,
        previous=previous,
        added=changes[0],
        modified=changes[1],
        deleted=changes[2],
        parsed=parsed,
        reused=reused,
    )
