"""The pinned LadybugDB FTS extension: where it lives, whether it can be loaded, and
``prepare_fts``, the only network access OXIDE makes (docs/platforms.md)."""

from __future__ import annotations

import hashlib
import os
import platform
import sys
import urllib.request
from importlib.metadata import version
from pathlib import Path

from oxide.models import InvalidPath, OxideError

FTS_VERSION = "0.21.0"
# (sys.platform, platform.machine()) -> the extension host's platform and the build's
# SHA-256: LadybugDB's v0.21.0 builds, mirrored unmodified on an OXIDE release.
FTS_BUILDS = {
    ("linux", "x86_64"): (
        "linux_amd64",
        "742f2756c2f80bcd1886b6ee0446483038cff0ecf6cf47f698bc6fe855cbaef6",
    ),
    ("darwin", "arm64"): (
        "osx_arm64",
        "248332de781a7781f612a55b5397e88f58f1f1ba2a973836ada199eab0f6bf10",
    ),
    ("darwin", "x86_64"): (
        "osx_amd64",
        "c86f427503555a3ccdc55eb66a8d82793a206aac599a6092f7d1d4eb50c21403",
    ),
    ("win32", "AMD64"): (
        "win_amd64",
        "7b6132ae6a554e3eeb3c4b0da7e2eb5c483b258b31e848a0e3d6000ac29885ee",
    ),
}
FTS_SCORER = "ladybug-fts-0.21.0-bm25;k1=1.2;b=0.75;stemmer=porter;disjunctive"
FTS_COMPONENT = "ladybug-fts-0.21.0;stemmer=porter;stopwords=default"


def fts_location() -> Path:
    if explicit := os.environ.get("OXIDE_FTS_EXTENSION"):
        return Path(explicit)
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".cache"
    return base / "oxide" / f"ladybug-extensions-{FTS_VERSION}" / "libfts.lbug_extension"


def _fts_build() -> tuple[str, str] | None:
    """The pinned extension's URL and SHA-256 for this platform, if there is one."""
    build = FTS_BUILDS.get((sys.platform, platform.machine()))
    if build is None:
        return None
    host, sha256 = build
    release = f"https://github.com/Kiy-K/oxide/releases/download/ladybug-fts-{FTS_VERSION}"
    return f"{release}/libfts-{FTS_VERSION}-{host}.lbug_extension", sha256


def _native_path_issue(text: str, variable: str) -> str | None:
    """Why LadybugDB cannot open this path, or None (docs/platforms.md). ``variable``
    names the environment variable that moves it."""
    if sys.platform == "win32" and not text.isascii():
        return f"{text!r}: on Windows LadybugDB opens only ASCII paths; set {variable}"
    return None


def fts_unavailable(path: Path) -> str | None:
    """Why the pinned FTS extension cannot be used, or None. Checked on every use:
    loading native code is a trust boundary (ADR-0002 § 3)."""
    if (build := _fts_build()) is None:
        return f"no pinned FTS extension for {sys.platform}/{platform.machine()}"
    if issue := _native_path_issue(str(path), "OXIDE_FTS_EXTENSION"):
        return issue
    try:
        data = path.read_bytes()
    except OSError:
        return f"FTS extension missing at {path}; run `oxide prepare`"
    if hashlib.sha256(data).hexdigest() != build[1]:
        return f"FTS extension at {path} does not match its pinned digest; run `oxide prepare`"
    return None


def prepare_fts(path: Path | None = None) -> Path:
    """Explicitly fetches the pinned FTS extension and verifies its digest. The only
    network access OXIDE makes; nothing calls it implicitly."""
    path = path or fts_location()
    if fts_unavailable(path) is None:
        return path
    if (build := _fts_build()) is None:
        raise OxideError(f"no pinned FTS extension for {sys.platform}/{platform.machine()}")
    if issue := _native_path_issue(str(path), "OXIDE_FTS_EXTENSION"):
        raise InvalidPath(issue)  # never download what could not be loaded
    url, sha256 = build
    path.parent.mkdir(parents=True, exist_ok=True)
    # The extension host refuses urllib's default User-Agent (HTTP 403, measured).
    request = urllib.request.Request(url, headers={"User-Agent": f"oxide/{version('oxide')}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except OSError as error:
        raise OxideError(f"could not fetch {url}: {error}") from error
    if hashlib.sha256(data).hexdigest() != sha256:
        raise OxideError(f"downloaded FTS extension does not match {sha256}")
    partial = path.with_suffix(".partial")
    partial.write_bytes(data)
    os.replace(partial, path)
    return path
