"""Persistent derived knowledge (LadybugDB)."""

from oxide.models import OxideError, ResourceExhausted, StoreCorrupt


def read_failure(directory: object, error: RuntimeError, what: str = "read failed") -> OxideError:
    """The error for a native failure opening or reading a sealed generation. A full buffer
    pool is ``ResourceExhausted``: the generation is intact, so nothing is rebuilt. Anything
    else is damage. Matches LadybugDB's message (ladybug 0.21); a reworded one falls back to
    ``StoreCorrupt``, which fails closed."""
    kind = ResourceExhausted if "buffer pool is full" in str(error) else StoreCorrupt
    return kind(f"{directory}: {what} ({error})")
