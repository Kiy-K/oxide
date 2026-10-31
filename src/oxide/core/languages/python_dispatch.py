"""Receiver-aware Python method lookup over the classes of one capture.

A method call resolves only where Python's attribute lookup is fixed by the source:
the receiver is a method's own first parameter (``self.m()``, ``cls.m()``), a zero-
argument ``super()``, a class (``Class.m()``), or a name bound exactly once, by calling
an in-repo class (``x = Class(); x.m()``). The candidates are the first definition of
``m`` along each C3 method resolution order the receiver can have: every in-repo
subclass for ``self``/``cls``/``super()``, the class itself otherwise. Lookup gives up,
leaving the call unresolved, wherever a definition could hide: a base outside the
repository or not a plain name, a class attribute or ``__getattribute__``, an attribute
``m`` assigned or deleted on a receiver of known class anywhere in the capture
(``self.m = ...`` in a method, ``x = C(); x.m = ...``, ``C.m = ...``) when that class is
on the receiver's method resolution order, an in-repo ``__new__`` for a constructed name.
An assignment through a receiver of unknown type (``response.m = ...``) is not evidence
about any class and hides nothing. Code outside the capture (subclasses elsewhere,
monkey-patching by ``setattr``) is not seen; that is the same closed world as cross-file
import resolution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import networkx as nx

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
        self._inheritance: nx.DiGraph[str] | None = None  # base -> derived
        self._assigned: dict[str, set[str]] | None = None  # class -> attributes set on it
        self._typing = False  # ``assigned`` is typing receivers

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
        import networkx as nx  # lazily: ADR-0012

        if self._inheritance is None:
            self._inheritance = nx.DiGraph()
            for derived in sorted(k for k in self.r.scopes if self.is_class(k)):
                self._inheritance.add_edges_from((b, derived) for b in self.bases(derived))
        graph = self._inheritance
        return sorted(nx.descendants(graph, key)) if key in graph else []

    def assigned(self) -> dict[str, set[str]]:
        """Attribute names assigned on each in-repo class or its instances, wherever the
        receiver's class is known: ``(receiver, name)`` facts typed like a call receiver."""
        if self._assigned is None:
            self._typing = True
            found: dict[str, set[str]] = {}
            for i, file in enumerate(self.r.files):
                bindings = self.r.bindings(i)
                for k, scope in enumerate(file.parsed.scopes):
                    for receiver, name in sorted(scope.assigned):
                        instance = self.r._instance(i, bindings, k, receiver)
                        res = self.r.lookup(i, k, receiver) if instance is None else None
                        if instance is not None:
                            found.setdefault(instance[0], set()).add(name)
                        elif res is not None and len(res) == 1 and self.is_class(res[0]):
                            found.setdefault(res[0], set()).add(name)
            self._assigned, self._typing = found, False
        return self._assigned

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
        if self._typing:  # a receiver of ``assigned`` made by a method call: unknown type
            return None
        assigned = self.assigned()
        if _private(name):  # mangled to ``_<cls>__name``: only ``cls``'s own body
            own = None if after or name in assigned.get(cls, ()) else self._defines(cls, name)
            return own or None
        targets: set[str] = set()
        for derived in [cls, *self.subclasses(cls)] if subclasses else [cls]:
            order = self.mro(derived)
            if cls not in order or any(name in assigned.get(c, ()) for c in order):
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
