"""Innermost-scope lookup by byte offset, shared by the language slices."""

from __future__ import annotations

import bisect


class Nesting:
    """The innermost scope whose body holds a byte, in O(log depth).

    Scope 0 is the whole file. Bodies nest, so the innermost scope holding a byte is an
    ancestor-or-self of the last body starting at or before it, and ancestors' bodies
    end no earlier. ``jumps[k][j]`` is the 2**j-th ancestor of ``k``: skipping the
    ancestors that end at or before the byte takes O(log depth), where one parent at
    a time is quadratic after a deep nest.
    """

    def __init__(self, size: int) -> None:
        self.bodies: list[tuple[int, int]] = []  # (body start, scope), sorted
        self.ends = [size]
        self.parents = [0]
        self.jumps: list[list[int]] = [[]]

    def add(self, parent: int, body: tuple[int, int]) -> None:
        own = len(self.ends)
        up = [parent]
        while len(self.jumps[up[-1]]) >= len(up):
            up.append(self.jumps[up[-1]][len(up) - 1])
        self.ends.append(body[1])
        self.parents.append(parent)
        self.jumps.append(up)
        bisect.insort(self.bodies, (body[0], own))

    def scope_at(self, at: int) -> int:
        i = bisect.bisect_right(self.bodies, (at, len(self.ends))) - 1
        k = self.bodies[i][1] if i >= 0 else 0
        if not k or at < self.ends[k]:
            return k
        for j in range(len(self.jumps[k]) - 1, -1, -1):
            if j < len(self.jumps[k]) and (up := self.jumps[k][j]) and self.ends[up] <= at:
                k = up
        return self.parents[k]
