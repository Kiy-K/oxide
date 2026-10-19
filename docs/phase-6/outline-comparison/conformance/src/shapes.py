"""Outline conformance: Python shapes."""
import os
import sys as system
from collections import (
    OrderedDict,
    defaultdict,
)
from . import sibling as alias

LIMIT = 10
names: list[str] = []


@decorator
@other.decorator(arg=1)
async def fetch(url: str, *, retries: int = 3) -> bytes:
    def inner() -> None:
        pass

    return b""


class Store(Base, metaclass=Meta):
    """A store."""

    capacity = 4
    label: str = "x"

    def __init__(self, path):
        self.path = path

    @property
    def size(self) -> int:
        return 0

    @staticmethod
    async def load(path) -> "Store":
        return Store(path)

    class Error(Exception):
        pass


@overload
def convert(x: int) -> int: ...
@overload
def convert(x: str) -> str: ...
def convert(x):
    return x


if system.version_info >= (3, 12):
    def compat() -> None:
        pass

try:
    import fast as speedups
except ImportError:
    speedups = None


def emoji_ñame(β: int) -> str:
    return "✓"
