import abc
from typing import Optional

DEFAULT_LIMIT = 10


@dataclass
class Record(abc.ABC):
    """A record."""

    @property
    def key(self) -> str:
        return self._key

    class Meta:
        table = "records"


class Plain(Record):
    def __init__(self, key: Optional[str]):
        self._key = key

    async def load(self):
        return await fetch(self._key)
