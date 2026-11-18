"""Shell syntax facts' data model (``shell`` extracts it, ``shell_facts`` caches it)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ShellDialect = Literal["bash", "sh"]

KINDS = frozenset({"function", "variable"})
DECL_KINDS = frozenset({"function", "variable"})
USE_KINDS = frozenset(
    {
        "command",
        "dynamic_command",
        "variable",
        "source",
        "dynamic_source",
        "script",
        "dynamic_script",
    }
)
DECLARATIONS = frozenset({"export", "readonly", "declare", "typeset", "local"})
INTERPRETERS = frozenset({"bash", "sh", "dash"})


@dataclass(slots=True)
class Decl:
    kind: str  # function | variable
    name: str
    start: int
    end: int
    signature: str
    owner: int | None  # enclosing function declaration; None at file level
    local: bool  # declared with `local` inside a function
    ordinal: int  # source-order index among same-scope declarations of the same name


@dataclass(frozen=True, slots=True)
class Use:
    name: str  # written name or path expression
    start: int
    end: int
    owner: int | None  # enclosing function declaration
    # command | dynamic_command | variable | source | dynamic_source | script | dynamic_script
    kind: str
    path: str  # normalized relative path when statically determinable, else ""


@dataclass(slots=True)
class Parsed:
    dialect: ShellDialect
    decls: list[Decl]
    uses: list[Use]
    diagnostics: list[str]
    bash_features: list[str]


def symbol_key(path: str, decl: Decl, decls: list[Decl]) -> str:
    """``symbol:<path>#<name>`` with enclosing functions prefixed; ``~n`` on duplicates."""
    parts = [decl.name]
    parent = decl.owner
    seen = 0
    while parent is not None and 0 <= parent < len(decls) and seen < 512:
        holder = decls[parent]
        parts.append(holder.name + (f"~{holder.ordinal}" if holder.ordinal else ""))
        parent = holder.owner
        seen += 1
    parts.reverse()
    if decl.ordinal:
        parts[-1] = f"{parts[-1]}~{decl.ordinal}"
    return f"symbol:{path}#" + ".".join(parts)
