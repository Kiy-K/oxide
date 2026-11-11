"""Navigation queries over one sealed generation (``ladybug.py`` is at its size limit)."""

from __future__ import annotations

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
