"""Missing-body notices for context bundles (issue #77).

A caller-named symbol packed as a header, or left out by the budget, must be
reported so the agent knows to raise the budget -- unless a final item already
contains its full source (e.g. its enclosing class packed complete), in which
case raising the budget would add nothing. Kept apart from ``packing`` so the
selection policy stays under the source-file size limit.
"""

from __future__ import annotations

from collections.abc import Sequence

from oxide.core.packing import Candidate
from oxide.models import ContextItem, Omission


def unpacked_bodies(
    pool: Sequence[Candidate],
    items: Sequence[ContextItem],
    omissions: Sequence[Omission],
) -> list[str]:
    """Degradation lines for symbol seeds whose source is absent from the payload
    (issue #77: a named symbol silently packed as a header). A caller-named symbol
    shown without its body, or left out by the budget, must be reported -- unless a
    final item already contains its full source (e.g. its enclosing class packed
    complete), in which case raising the budget would not add anything."""
    wanted = {
        c.entity.key: c.entity.source
        for c in pool
        if c.seeded
        and any(s.startswith("symbol:") for s in c.seeds)
        and c.entity.source is not None
    }
    missing = {
        e.key for item in items for e in item.entities if e.key in wanted and not e.complete
    } | {o.key for o in omissions if o.stage == "packing" and o.key in wanted}
    covered = {
        key
        for key, source in wanted.items()
        if any(
            item.source.path == source.path
            and item.source.digest == source.digest
            and item.source.start <= source.start
            and source.end <= item.source.end
            for item in items
        )
    }
    return [f"{key} packed without its body; raise the budget" for key in sorted(missing - covered)]
