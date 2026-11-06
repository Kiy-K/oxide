"""Source capture (ADR-0010 § Source capture): one consistent, immutable read of a worktree.

Lists, stats and reads only; never runs Git, hooks or repository code. Symlinks are
never followed, special files never opened, and every skipped in-scope entry is
reported and part of the snapshot identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pathspec

from oxide._winstat import change_time
from oxide.models import CaptureConflict, CaptureLimit, InvalidPath, Skip, SkipReason

CAPTURE_VERSION = "oxide-capture-v3"
MAX_FILE_BYTES = 1 << 20
MAX_TOTAL_BYTES = 1 << 30
EXCLUDED = frozenset({".git", ".hg", ".jj", ".svn", ".oxide"})
ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class CapturedFile:
    path: str
    data: bytes
    digest: str


@dataclass(frozen=True, slots=True)
class Capture:
    root: Path
    snapshot_id: str
    files: tuple[CapturedFile, ...]
    skipped: tuple[Skip, ...]
    case_collisions: tuple[tuple[str, ...], ...]


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# Include permissions and ctime: restoring mtime must not hide edits or unreadable files.
type StatKey = tuple[int, int, int, int, int, int]
type CaptureCache = dict[str, tuple[StatKey, CapturedFile]]

_WINDOWS = sys.platform == "win32"


def _key(st: os.stat_result, source: str | int | None = None) -> StatKey:
    """``source`` is the file path or descriptor ``st`` came from; without it (directories,
    whose listing is never cached) the Windows change time is left out."""
    if _WINDOWS:
        # Neither the creation-time st_ctime nor mode bits (path and handle stats disagree on
        # them) are usable here. shortcut: an ACL change keeps this key, so a cached file made
        # unreadable stays captured until its next edit; Windows has no cheap signal for it.
        changed = 0 if source is None else change_time(source)
        return (stat.S_IFMT(st.st_mode), st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino, changed)
    return (st.st_mode, st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino, st.st_ctime_ns)


@dataclass(slots=True)
class _Listing:
    files: dict[str, StatKey]
    state: dict[str, object]
    skipped: list[Skip]
    ignores: list[tuple[str, str]]


def _ignored(rules: list[tuple[str, pathspec.GitIgnoreSpec]], path: str, is_dir: bool) -> bool:
    # Git precedence: deeper .gitignore files override shallower ones, last match wins.
    ignored = False
    for base, spec in rules:
        relative = path[len(base) + 1 :] if base else path
        result = spec.check_file(relative + "/" if is_dir else relative)
        if result.include is not None:
            ignored = result.include
    return ignored


# Windows lacks O_NOFOLLOW/O_NONBLOCK (identity checks catch a swapped path) and needs
# O_BINARY, or CRLF reads come back short.
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)


def _walk(root: Path, excluded: frozenset[tuple[int, int]]) -> _Listing:
    listing = _Listing({}, {}, [], [])
    stack: list[tuple[str, list[tuple[str, pathspec.GitIgnoreSpec]]]] = [("", [])]
    while stack:
        directory, rules = stack.pop()
        absolute = root / directory if directory else root
        try:
            with os.scandir(absolute) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            listing.skipped.append(Skip(directory, "unreadable"))
            continue
        ignore = next((e for e in entries if e.name == ".gitignore"), None)
        if ignore is not None and ignore.is_file(follow_symlinks=False):
            path = f"{directory}/.gitignore" if directory else ".gitignore"
            # Rules that cannot be read or decoded fail the capture closed (ADR-0010).
            try:
                with os.fdopen(os.open(absolute / ".gitignore", _READ_FLAGS), "rb") as file:
                    data = file.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    raise CaptureLimit(f"{path}: ignore rules exceed {MAX_FILE_BYTES} bytes")
                lines = data.decode("utf-8").splitlines()
            except (OSError, UnicodeDecodeError) as error:
                raise CaptureConflict(f"{path}: unreadable ignore rules ({error})") from error
            listing.ignores.append((path, digest(data)))
            rules = [*rules, (directory, pathspec.GitIgnoreSpec.from_lines(lines))]
        for entry in entries:
            if entry.name in EXCLUDED:
                continue
            path = f"{directory}/{entry.name}" if directory else entry.name
            try:
                path.encode("utf-8")
            except UnicodeEncodeError:
                # Never an entity; reported with its raw bytes escaped. A directory's
                # whole subtree is skipped.
                raw = os.fsencode(path).decode("utf-8", "backslashreplace")
                listing.skipped.append(Skip(raw, "invalid_name"))
                continue
            try:
                # Not entry.stat(): on Windows it reports st_dev and st_ino as zero.
                st = os.lstat(entry.path)
            except OSError:
                listing.skipped.append(Skip(path, "unreadable"))
                continue
            is_dir = stat.S_ISDIR(st.st_mode)
            if is_dir and (st.st_dev, st.st_ino) in excluded:
                continue
            if _ignored(rules, path, is_dir):
                continue
            reason: SkipReason | None = None
            if stat.S_ISLNK(st.st_mode):
                reason = "symlink"
            elif is_dir:
                listing.state[path + "/"] = _key(st)
                stack.append((path, rules))
            elif not stat.S_ISREG(st.st_mode):
                reason = "special"
            elif st.st_size > MAX_FILE_BYTES:
                reason = "too_large"
            else:
                try:
                    listing.files[path] = _key(st, entry.path)
                except OSError:  # no change time on Windows: never cached, so reported
                    reason = "unreadable"
            if reason is not None:
                listing.skipped.append(Skip(path, reason))
    listing.state.update(listing.files)
    listing.state["skipped"] = sorted((s.path, s.reason) for s in listing.skipped)
    listing.state["ignores"] = sorted(listing.ignores)
    return listing


def _read(root: Path, path: str, expected: StatKey) -> bytes | Literal[False] | None:
    """The file's bytes; None when it changed since it was listed; False when it
    cannot be opened for want of permission (an ``unreadable`` skip once the final
    walk confirms the tree did not change)."""
    try:
        fd = os.open(root / path, _READ_FLAGS)
    except PermissionError:
        return False
    except OSError:
        return None
    with os.fdopen(fd, "rb") as handle:
        if _key(os.fstat(fd), fd) != expected:
            return None
        data = handle.read(expected[1] + 1)  # a file growing after fstat stays bounded
    return data if len(data) == expected[1] else None


def capture(
    root: str | os.PathLike[str], exclude: Iterable[Path] = (), *, cache: CaptureCache | None = None
) -> Capture:
    """Captures ``root``; retries when the tree changes underneath, then fails closed.

    Directories in ``exclude`` (OXIDE's own data location) are left out wherever they
    sit, matched by device and inode, so a symlinked or differently spelled path to
    the same directory is excluded too. Absent ones are ignored."""
    try:
        base = Path(root).resolve(strict=True)
    except OSError as error:
        raise InvalidPath(f"{root}: {error.strerror}") from error
    if not base.is_dir():
        raise InvalidPath(f"{base}: not a directory")
    excluded: set[tuple[int, int]] = set()
    for directory in exclude:
        try:
            st = os.stat(directory)
        except OSError:
            continue
        excluded.add((st.st_dev, st.st_ino))
    frozen = frozenset(excluded)
    for _ in range(ATTEMPTS):
        listing = _walk(base, frozen)
        total = 0
        files: list[CapturedFile] = []
        unreadable: list[Skip] = []
        next_cache: CaptureCache = {}
        for path, expected in sorted(listing.files.items()):
            total += expected[1]
            if total > MAX_TOTAL_BYTES:
                raise CaptureLimit(f"{base}: more than {MAX_TOTAL_BYTES} bytes in scope")
            cached = cache.get(path) if cache is not None else None
            if cached is not None and cached[0] == expected:
                files.append(cached[1])
                next_cache[path] = cached
                continue
            data = _read(base, path, expected)
            if data is None:
                break
            if data is False:
                unreadable.append(Skip(path, "unreadable"))
                continue
            captured = CapturedFile(path, data, digest(data))
            files.append(captured)
            next_cache[path] = (expected, captured)
        else:
            if _walk(base, frozen).state == listing.state:
                listing.skipped += unreadable
                if cache is not None:
                    cache.clear()
                    cache.update(next_cache)
                return _seal(base, files, listing)
    raise CaptureConflict(f"{base}: changed during capture {ATTEMPTS} times")


def _seal(root: Path, files: list[CapturedFile], listing: _Listing) -> Capture:
    skipped = sorted(listing.skipped, key=lambda s: (s.path, s.reason))
    manifest = [
        CAPTURE_VERSION,
        MAX_FILE_BYTES,
        sorted(EXCLUDED),
        [[f.path, f.digest] for f in files],
        [[s.path, s.reason] for s in skipped],
        sorted(listing.ignores),
    ]
    encoded = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode()
    folded: dict[str, list[str]] = {}
    for f in files:
        folded.setdefault(f.path.lower(), []).append(f.path)
    collisions = tuple(tuple(group) for group in folded.values() if len(group) > 1)
    return Capture(root, digest(encoded), tuple(files), tuple(skipped), collisions)
