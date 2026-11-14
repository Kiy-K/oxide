"""Human rendering of ``oxide outline``, ``inspect`` and ``refs``; ``--json`` prints the result."""

from __future__ import annotations

from oxide.models_nav import Candidate, Declaration, Inspection, Link, Outline, References


def _where(d: Declaration) -> str:
    if d.location is None:
        return ""
    a = d.location
    return f"{a.path}:{a.start_line}" + (f"-{a.end_line}" if a.end_line != a.start_line else "")


def _head(d: Declaration) -> str:
    return (d.signature or f"{d.kind} {d.name}") + (" [test]" if d.test else "")


def outline_text(outline: Outline) -> str:
    out = [f"{outline.target} @ {outline.snapshot.snapshot_id[:12]}"]
    for file in outline.files:
        out.append(f"{file.path} ({file.language}, {file.coverage})")
        for d in file.declarations:
            a = d.location
            span = f"{a.start_line}-{a.end_line}" if a else ""
            out.append(f"  {'  ' * d.depth}{span:>9}  {_head(d)}")
        if file.truncated:
            out.append("  … declarations cut by --limit")
    if outline.unsupported:
        out.append(f"{outline.unsupported} unsupported files not listed (file-level only)")
    if outline.truncated:
        out.append("truncated: raise --limit to see the rest")
    return "\n".join(out)


def _candidates(items: tuple[Candidate, ...], truncated: bool) -> list[str]:
    out = [
        f"  [{c.why}] {_head(c.declaration)}  {_where(c.declaration)}  {c.declaration.key}"
        for c in items
    ]
    return [*out, "  … results may be incomplete"] if truncated else out


def _unresolved(status: str, query: str, items: tuple[Candidate, ...], truncated: bool) -> str:
    if status == "not_found":
        if truncated:
            return (
                "resolution truncated; if --limit is 0, increase it; "
                "otherwise use an exact entity key"
            )
        return f"no match for {query!r}; find names with `oxide search` or `oxide outline`"
    head = f"{query!r} is ambiguous; repeat with an entity key, path#Qualified.name or path:line"
    return "\n".join([head, *_candidates(items, truncated)])


def _link(link: Link) -> str:
    counts = (
        ("proven", link.proven),
        ("heuristic", link.heuristic),
        ("ambiguous", link.ambiguous),
        ("unresolved", link.unresolved),
    )
    return f"  {link.direction:>3} {link.kind}: " + ", ".join(f"{n} {k}" for k, n in counts if n)


def inspect_text(result: Inspection) -> str:
    if result.status != "resolved":
        return _unresolved(result.status, result.query, result.candidates, result.truncated)
    d = result.candidates[0].declaration
    out = [f"{_head(d)}", f"  {_where(d)}", f"  key {d.key}"]
    if result.parent:
        out.append(f"in {_head(result.parent)}  {_where(result.parent)}")
    if result.members:
        out.append("members:")
        out += [f"  {_head(m)}  {_where(m)}" for m in result.members]
        if result.truncated:
            out.append("  … more members than --limit")
    if result.links:
        out.append("relations (see `oxide refs`):")
        out += [_link(link) for link in result.links]
    return "\n".join(out)


def refs_text(result: References) -> str:
    if result.status != "resolved":
        return _unresolved(result.status, result.query, result.candidates, result.truncated)
    d = result.candidates[0].declaration
    out = [f"{_head(d)}  {_where(d)}"]
    for r in result.references:
        at = f"{r.at.path}:{r.at.start_line}" if r.at else "-"
        far = f"{r.other.kind} {r.other.name} ({_where(r.other)})" if r.other else (r.name or "?")
        out.append(f"  {r.label:<10} {r.direction:>3} {r.kind:<10} {far}  @ {at}")
        if r.text:
            out.append(f"             {r.text}")
    if result.truncated:
        out.append("  … cut by --limit")
    if result.unresolved_same_name:
        out.append(
            f"{result.unresolved_same_name} call/reference sites of this name were not resolved "
            "to any entity and may include uses of it"
        )
    out.append("evidence OXIDE derived, not an exhaustive list of uses")
    return "\n".join(out)
