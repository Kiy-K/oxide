"""Receiver-aware Python method lookup over the classes of one capture.

A method call resolves only where Python's attribute lookup is fixed by the source:
the receiver is a method's own first parameter (``self.m()``, ``cls.m()``), a zero-
argument ``super()``, a class (``Class.m()``), or a name bound exactly once, by calling
an in-repo class (``x = Class(); x.m()``). The candidates are the first definition of
``m`` along each C3 method resolution order the receiver can have: every in-repo
subclass for ``self``/``cls``/``super()``, the class itself otherwise. Lookup gives up,
leaving the call unresolved, wherever a definition could hide: a base outside the
repository or not a plain name, a class attribute or ``__getattribute__``, an attribute
assigned anywhere (``x.m = ...``), an in-repo ``__new__`` for a constructed name.
Code outside the capture (subclasses elsewhere, monkey-patching by ``setattr``) is not
seen; that is the same closed world as cross-file import resolution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from oxide.core.languages.python_resolve import Res, Resolver


def _private(name: str) -> bool:
    return name.startswith("__") and not name.endswith("__")


class Dispatch:
    def __init__(self, resolver: Resolver) -> None:
        self.r = resolver
        self.attrs: set[str] = set().union(*(f.parsed.attrs for f in resolver.files))
        self._bases: dict[str, list[str]] = {}
        self._mro: dict[str, list[str]] = {}
        self._active: list[str] = []  # classes whose MRO is being computed, outermost first
        self._cyclic: set[str] = set()
        self._subclasses: dict[str, list[str]] | None = None

    def is_class(self, key: str) -> bool:
        if key not in self.r.scopes:
            return False
        i, k = self.r.scopes[key]
        decl = self.r.files[i].parsed.scopes[k].decl
        return decl is not None and decl.cls

    def bases(self, key: str) -> list[str]:
        """Direct bases in order; ``?`` tokens stand for bases not known to be in-repo
        classes (their own ancestors are unknown)."""
        if key not in self._bases:
            i, k = self.r.scopes[key]
            scope = self.r.files[i].parsed.scopes[k]
            assert scope.parent is not None
            found = []
            for n, base in enumerate(scope.bases):
                res = self.r.lookup(i, scope.parent, base.name)
                known = res is not None and len(res) == 1 and self.is_class(res[0])
                found.append(res[0] if res and known else f"?{key}:{n}")
            self._bases[key] = [*found, f"?{key}:open"] if scope.open else found
        return self._bases[key]

    def mro(self, key: str) -> list[str]:
        """C3 linearization; ``[key, ?]`` when inconsistent or in a base cycle."""
        if key in self._mro:
            return self._mro[key]
        if key in self._active:  # a base cycle: every class on it is invalid
            self._cyclic.update(self._active[self._active.index(key) :])
            return [key, f"?{key}:cycle"]
        self._active.append(key)
        bases = self.bases(key)
        pending = [[*self.mro(b)] if not b.startswith("?") else [b] for b in bases]
        pending.append(list(bases))
        out = [key]
        while pending := [p for p in pending if p]:
            head = next((p[0] for p in pending if all(p[0] not in q[1:] for q in pending)), None)
            if head is None:
                out = [key, f"?{key}:conflict"]
                break
            out.append(head)
            for p in pending:
                if p[0] == head:
                    p.pop(0)
        self._active.pop()
        self._mro[key] = [key, f"?{key}:cycle"] if key in self._cyclic else out
        return self._mro[key]

    def subclasses(self, key: str) -> list[str]:
        """Every in-repo class with ``key`` among its known bases, transitively."""
        if self._subclasses is None:
            self._subclasses = {}
            for derived in sorted(k for k in self.r.scopes if self.is_class(k)):
                for base in self.bases(derived):
                    self._subclasses.setdefault(base, []).append(derived)
        found, stack = [], [key]
        seen = {key}
        while stack:
            for derived in self._subclasses.get(stack.pop(), []):
                if derived not in seen:
                    seen.add(derived)
                    found.append(derived)
                    stack.append(derived)
        return sorted(found)

    def _defines(self, key: str, name: str) -> Res | Literal[False]:
        """Candidates ``key``'s body binds to ``name``; False when it does not bind it,
        None when it binds it some other way or may intercept every lookup."""
        i, k = self.r.scopes[key]
        if self.r.in_scope(i, k, "__getattribute__", []) is not False:
            return None
        return self.r.in_scope(i, k, name, [])

    def method(self, cls: str, name: str, subclasses: bool, after: bool = False) -> Res:
        """Targets of ``name`` looked up on an instance (``subclasses``: of ``cls`` or any
        subclass) or on ``cls`` itself, starting after ``cls`` for ``super()``."""
        self.r.inferred = True
        if name in self.attrs:
            return None
        if _private(name):  # mangled to ``_<cls>__name``: only ``cls``'s own body
            own = None if after else self._defines(cls, name)
            return own or None
        targets: set[str] = set()
        for derived in [cls, *self.subclasses(cls)] if subclasses else [cls]:
            order = self.mro(derived)
            if cls not in order:
                return None
            for candidate in order[order.index(cls) + 1 if after else 0 :]:
                found = None if candidate.startswith("?") else self._defines(candidate, name)
                if found is None:
                    return None
                if found is not False:
                    targets.update(found)
                    break
            else:
                return None
        return sorted(targets) or None

    def constructs(self, cls: str) -> bool:
        """Whether calling ``cls`` makes an instance of exactly ``cls``: no metaclass or
        unrecorded base, and no in-repo ``__new__``."""
        i, k = self.r.scopes[cls]
        if self.r.files[i].parsed.scopes[k].open:
            return False
        for candidate in self.mro(cls):
            if candidate.startswith("?"):
                return True
            if self._defines(candidate, "__new__") is not False:
                return False
        return True
