"""Receiver-aware Python method calls: resolved only where the source fixes the target."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import write

import oxide

BASE = """\
class Base:
    def run(self):
        self.step()
        self.helper()
        self.missing()
        self.handler()
        self.__secret()
        self.value.get()

    def step(self): pass
    def helper(self): pass
    def __secret(self): pass

    def setup(self):
        self.handler = print

    def handler(self): pass

    @staticmethod
    def static(self):
        self.helper()

    @classmethod
    def make(cls):
        return cls.helper()

    def rebinds(self):
        self = Base()
        self.helper()

    def nested(self):
        def inner():
            self.helper()
        inner()
"""

CHILD = """\
from .base import Base
from external import Mixin

class Child(Base):
    def step(self):
        super().step()
        super().missing()

    def __secret(self): pass

class Grand(Child):
    pass

class Other:
    def helper(self): pass
    def go(self):
        self.helper()

class Mixed(Mixin, Other):
    pass

class Meta(Base, metaclass=type):
    pass

def use():
    b = Base()
    b.helper()
    b.step()
    Child.helper()
    Grand.step()
    c = Child()
    c = Child()
    c.step()
    m = Meta()
    m.helper()
    x = make()
    x.helper()
"""

CYCLE = """\
class A(B):
    def f(self):
        self.g()

class B(A):
    def g(self): pass
"""

SOURCES = {"pkg/__init__.py": "", "pkg/base.py": BASE, "pkg/child.py": CHILD, "cyc.py": CYCLE}


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[oxide.Repository]:
    with oxide.open_repository(write(tmp_path / "src", SOURCES)) as repository:
        repository.index()
        yield repository


def calls(
    repo: oxide.Repository, key: str, sources: dict[str, str] = SOURCES
) -> list[tuple[str, list[str] | None]]:
    """(call text, sorted target names or None when unresolved) per call site, in order."""
    sites: dict[int, tuple[str, list[str]]] = {}
    for r in repo.relations(key):
        if r.kind == "calls" and r.source == key and r.evidence is not None:
            text = sources[r.evidence.path][r.evidence.start : r.evidence.end]
            _, found = sites.setdefault(r.site, (text.removesuffix("()"), []))
            if r.target is not None:
                found.append(r.target.rsplit("#", 1)[1])
    return [(text, sorted(found) or None) for _, (text, found) in sorted(sites.items())]


def test_self_dispatch_counts_every_in_repo_override(repo: oxide.Repository) -> None:
    assert calls(repo, "symbol:pkg/base.py#Base.run") == [
        ("self.step", ["Base.step", "Child.step"]),  # Child overrides it
        ("self.helper", ["Base.helper"]),
        ("self.missing", None),
        ("self.handler", None),  # also assigned as an instance attribute
        ("self.__secret", ["Base.__secret"]),  # name-mangled: never Child.__secret
        ("self.value.get", None),  # an attribute of an attribute
    ]
    run = repo.relations("symbol:pkg/base.py#Base.run")
    by_target = {r.target: (r.resolution, r.basis) for r in run if r.kind == "calls"}
    # Dispatch rests on the capture's whole class hierarchy: a heuristic basis, so
    # ``impact`` never counts it as proven and the router does not follow it.
    assert by_target["symbol:pkg/base.py#Base.helper"] == ("resolved", "heuristic")
    assert by_target["symbol:pkg/child.py#Child.step"] == ("ambiguous", "heuristic")


def test_receivers_closures_and_classmethods(repo: oxide.Repository) -> None:
    assert calls(repo, "symbol:pkg/base.py#Base.static") == [("self.helper", None)]
    assert calls(repo, "symbol:pkg/base.py#Base.rebinds")[-1] == ("self.helper", None)
    assert calls(repo, "symbol:pkg/base.py#Base.nested.inner") == [("self.helper", ["Base.helper"])]
    assert calls(repo, "symbol:pkg/base.py#Base.make") == [("cls.helper", ["Base.helper"])]


def test_super_and_external_bases(repo: oxide.Repository) -> None:
    assert calls(repo, "symbol:pkg/child.py#Child.step") == [
        ("super", None),
        ("super().step", ["Base.step"]),
        ("super", None),
        ("super().missing", None),
    ]
    # ``Mixed`` puts an external class before ``Other`` in its MRO: it may define helper.
    assert calls(repo, "symbol:pkg/child.py#Other.go") == [("self.helper", None)]


def test_classes_and_constructed_names_resolve_exactly(repo: oxide.Repository) -> None:
    found = [c for c in calls(repo, "symbol:pkg/child.py#use") if "." in c[0]]
    assert found == [
        ("b.helper", ["Base.helper"]),
        ("b.step", ["Base.step"]),  # exactly a Base: no subclass dispatch
        ("Child.helper", ["Base.helper"]),  # inherited through the MRO
        ("Grand.step", ["Child.step"]),
        ("c.step", None),  # bound twice
        ("m.helper", None),  # a metaclass may change what calling the class makes
        ("x.helper", None),  # ``make`` is not a class
    ]


def test_base_cycles_resolve_nothing(repo: oxide.Repository) -> None:
    assert calls(repo, "symbol:cyc.py#A.f") == [("self.g", None)]


def test_overrides_added_and_removed_incrementally(tmp_path: Path) -> None:
    root = write(tmp_path / "src", {"pkg/__init__.py": "", "pkg/base.py": BASE})
    step = ("self.step", ["Base.step"])
    with oxide.open_repository(root) as repo:
        repo.index()
        assert calls(repo, "symbol:pkg/base.py#Base.run")[0] == step
        (root / "pkg/sub.py").write_text(
            "from .base import Base\nclass Sub(Base):\n    def step(self): pass\n"
        )
        repo.index()
        assert calls(repo, "symbol:pkg/base.py#Base.run")[0] == (
            "self.step",
            ["Base.step", "Sub.step"],
        )
        (root / "pkg/sub.py").unlink()
        repo.index()
        assert calls(repo, "symbol:pkg/base.py#Base.run")[0] == step


def test_self_referential_bindings_terminate(tmp_path: Path) -> None:
    source = (
        "class A:\n    def m(self): pass\n\n"
        "def f(x):\n    y = y.m()\n    a = b.m()\n    b = a.m()\n"
    )
    with oxide.open_repository(write(tmp_path / "src", {"mod.py": source})) as repo:
        repo.index()
        found = [r for r in repo.relations("symbol:mod.py#f") if r.kind == "calls"]
        assert {r.resolution for r in found} == {"unresolved"}


SHADOWS = {
    "session.py": """\
class Session:
    def request(self): pass
    def get(self):
        return self.request()

class Response:
    def __init__(self):
        self.request = None

def build(req):
    response = Response()
    response.request = req

def patch(anything, req):
    anything.request = req
""",
    "shadow.py": """\
class Base:
    def setup(self):
        self.hook = print

class Child(Base):
    def hook(self): pass
    def other(self):
        self.hook()

class Plain:
    def hook(self): pass
    def go(self):
        self.hook()

class Client:
    def send(self): pass
    def go(self):
        self.send()

class Patched:
    def run(self): pass
    def go(self):
        self.run()

Patched.run = print

def use():
    c = Client()
    c.send = print
    c.send()
""",
}


def test_attributes_hide_methods_only_on_their_own_class_hierarchy(tmp_path: Path) -> None:
    with oxide.open_repository(write(tmp_path / "src", SHADOWS)) as repo:
        repo.index()

        def site(key: str) -> list[tuple[str, list[str] | None]]:
            return calls(repo, key, SHADOWS)

        # requests: ``response.request = req`` sets a Response, never a Session; an
        # untyped receiver (``anything``) is no evidence about any class.
        assert site("symbol:session.py#Session.get") == [("self.request", ["Session.request"])]
        assert site("symbol:shadow.py#Plain.go") == [("self.hook", ["Plain.hook"])]
        # Real shadowing still resolves nothing: an instance attribute set by a base
        # class's method, on a constructed instance, or a class attribute.
        assert site("symbol:shadow.py#Child.other") == [("self.hook", None)]
        assert site("symbol:shadow.py#Client.go") == [("self.send", None)]
        assert site("symbol:shadow.py#use") == [("Client", ["Client"]), ("c.send", None)]
        assert site("symbol:shadow.py#Patched.go") == [("self.run", None)]
