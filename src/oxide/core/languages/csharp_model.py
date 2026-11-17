"""The C# syntax facts' data model (``csharp`` extracts it, ``csharp_facts`` caches it)."""

from __future__ import annotations

from dataclasses import dataclass, field

from oxide.core.languages.python import Use


@dataclass(slots=True)
class Decl:
    kind: str
    name: str
    start: int
    end: int
    signature: str
    ns: str  # the namespace the declaration is in, "" for the global one
    tparams: int  # declared type parameters
    arity: int  # declared parameters of a method, constructor or delegate; 0 otherwise
    optional: int  # of those, parameters with a default value
    varargs: bool  # a trailing ``params`` parameter
    test: bool  # a method with a test attribute
    ptypes: str  # the parameter types as written, ``,``-joined: tells overloads apart
    local: bool  # a ``file`` type, visible in its own file only


@dataclass(frozen=True, slots=True)
class Import:
    """``using N;`` (``kind`` "using"), ``using static T;`` ("static") or ``using A = X;``
    ("alias", named ``alias``); ``ns`` is the namespace block it stands in."""

    kind: str
    path: str  # dotted, without type arguments
    alias: str
    ns: str
    is_global: bool
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Call:
    """``m``, ``this.m``, ``base.m``, ``x.m``, ``this.x.m`` or ``A.B.m``; ``new T`` for an
    object creation (a space never occurs in a C# name)."""

    name: str
    start: int
    end: int
    arity: int


@dataclass(slots=True)
class Scope:
    """A declared type or member; scope 0 is the file (``decl`` None)."""

    parent: int | None  # None for scope 0, the file
    decl: Decl | None
    supers: list[Use] = field(default_factory=list)  # named base types of a type scope
    bound: dict[str, str | None] = field(default_factory=dict)  # variable -> declared type
    types: set[str] = field(default_factory=set)  # type parameters and local functions
    calls: list[Call] = field(default_factory=list)
    refs: list[Use] = field(default_factory=list)
    attrs: list[Use] = field(default_factory=list)  # attribute names: ``N`` or ``NAttribute``


@dataclass(slots=True)
class Parsed:
    imports: list[Import]
    scopes: list[Scope]
    diagnostics: list[str]
    blocks: list[tuple[int, int, str]]  # namespace blocks: (start, end, full namespace)
