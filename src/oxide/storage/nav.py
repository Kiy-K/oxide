"""Navigation queries over one sealed generation (``ladybug.py`` is at its size limit)."""

from __future__ import annotations

from collections.abc import Sequence

from oxide.models import Entity
from oxide.storage.ladybug import _ENTITY, View, _entity


def declarations(view: View, paths: list[str]) -> list[Entity]:
    """Symbols of ``paths`` in source order: outermost first at equal starts."""
    if not paths:
        return []
    rows = view._query(
        f"MATCH (e:Entity) WHERE e.category = 'symbol' AND e.path IN $paths RETURN {_ENTITY} "
        "ORDER BY e.path, e.start_byte, e.end_byte DESC, e.key",
        {"paths": sorted(set(paths))},
    )
    return [_entity(row) for row in rows]


def named(
    view: View, name: str, limit: int, paths: Sequence[str] | None = None
) -> tuple[list[str], bool]:
    """Keys of entities named ``name``, optionally restricted to ``paths``, ordered by key."""
    if paths is not None:
        if not paths:
            return [], False
        rows = view._query(
            "MATCH (e:Entity) WHERE e.name = $name AND e.path IN $paths "
            "RETURN e.key ORDER BY e.key LIMIT $n",
            {"name": name, "paths": list(paths), "n": limit + 1},
        )
    else:
        rows = view._query(
            "MATCH (e:Entity) WHERE e.name = $name RETURN e.key ORDER BY e.key LIMIT $n",
            {"name": name, "n": limit + 1},
        )
    return [r[0] for r in rows[:limit]], len(rows) > limit
