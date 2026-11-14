"""Read the last published repository status without owning or updating the store."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from oxide.models import InvalidPath, RepositoryStatus, Skip, SkipReason, StoreCorrupt
from oxide.storage.ladybug import default_data_dir
from oxide.storage.reader import Reader


def repository_status(
    path: str | os.PathLike[str], *, data_dir: str | os.PathLike[str] | None = None
) -> RepositoryStatus:
    """Read one published snapshot; raise NotIndexed if none exists."""
    try:
        root = Path(path).resolve(strict=True)
    except OSError as error:
        raise InvalidPath(f"{path}: {error.strerror}") from error
    if not root.is_dir():
        raise InvalidPath(f"{root}: not a directory")
    reader = Reader(root, Path(data_dir or default_data_dir()).absolute())
    try:
        view = reader.view()
        view.verify_sources()
        try:
            raw_skips = view.manifest["skipped"]
            raw_collisions = view.manifest["case_collisions"]
            if not isinstance(raw_skips, list) or not isinstance(raw_collisions, list):
                raise ValueError("invalid skipped or case_collisions")
            skips = []
            for item in raw_skips:
                if (
                    not isinstance(item, list)
                    or len(item) != 2
                    or not isinstance(item[0], str)
                    or item[1]
                    not in ("symlink", "special", "too_large", "unreadable", "invalid_name")
                ):
                    raise ValueError("invalid skipped entry")
                skips.append(Skip(item[0], cast(SkipReason, item[1])))
            collisions = []
            for group in raw_collisions:
                if not isinstance(group, list) or not all(isinstance(p, str) for p in group):
                    raise ValueError("invalid case collision")
                collisions.append(tuple(group))
        except (KeyError, TypeError, ValueError) as error:
            raise StoreCorrupt(f"{view.directory}: malformed manifest ({error})") from error
        return RepositoryStatus(
            snapshot=view.snapshot,
            files=tuple(sorted(view.files.values(), key=lambda item: item.path)),
            entities=view.counts[0],
            relations=view.counts[1],
            skipped=tuple(skips),
            case_collisions=tuple(collisions),
            lexical_unavailable=view.lexical_unavailable,
        )
    finally:
        reader.close()
