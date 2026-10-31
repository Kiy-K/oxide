"""The arms (preregistration.md § Arms): one value, several model-facing texts.

Search (``mode=search``) arms render ``asdict(SearchResult)`` normalized through JSON:

- ``C``  production: compact JSON in dataclass order (FastMCP's own text, verified equal).
- ``CM`` C with two leading fields, ``entries_returned`` and ``more_available``. Accurate:
         both are computed from fields the result already carries.
- ``CL`` C with a line break before each entry and before the trailing status (still JSON).
- ``P``  pretty JSON, ``indent=2`` (CLI ``--json``; also what OpenCode code mode shows).
- ``E``  C between a status header that announces the end marker, and that marker (not JSON).

Context (``mode=context``) arms render the production text, a summary line + payload:

- ``C``  production (``oxide.mcp_server``), verified equal.
- ``E``  C between a line that announces the end marker, and that marker.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

SEARCH_ARMS = ("C", "CM", "CL", "P", "E")
CONTEXT_ARMS = ("C", "E")
ENDS = 'the complete output ends with the line "# end of oxide {}: …"'


def plain(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=dataclasses.asdict))


def compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def more(v: dict[str, Any]) -> bool:
    """Whether the server itself left matches out: its lexical limit, or a symbol hint
    with more matches than it seeds."""
    return bool(v["lexical"]["truncated"]) or any(h["issue"] == "truncated" for h in v["hints"])


def counted(v: dict[str, Any]) -> dict[str, Any]:
    return {"entries_returned": len(v["entries"]), "more_available": more(v)} | v


def search_text(v: dict[str, Any], arm: str) -> str:
    if arm == "C":
        return compact(v)
    if arm == "CM":
        return compact(counted(v))
    if arm == "P":
        return json.dumps(v, indent=2, ensure_ascii=False)
    if arm == "CL":  # assumes the dataclass order: snapshot, query, entries, then status
        head = compact({k: v[k] for k in ("snapshot", "query")})[:-1]
        tail = compact({k: x for k, x in v.items() if k not in ("snapshot", "query", "entries")})
        body = ",\n".join(compact(e) for e in v["entries"])
        return f'{head},"entries":[\n{body}\n],\n{tail[1:]}'
    if arm == "E":
        n = len(v["entries"])
        status = "more matches exist beyond the result limit" if more(v) else "no more matches"
        return (f"# oxide search: {n} entries; {status}; {ENDS.format('search')}\n"
                f"{compact(v)}\n# end of oxide search: {n} entries\n")  # fmt: skip
    raise ValueError(arm)


def context_text(v: dict[str, Any], arm: str) -> str:
    """``v`` is ``asdict(ContextBundle)``; the payload is the items' text, in order."""
    head = (
        f"# oxide context: {v['used']}/{v['budget']} {v['units']}, "
        f"{len(v['items'])} items, {len(v['omissions'])} omitted, "
        f"snapshot {v['snapshot']['snapshot_id']}\n"
    )
    head += "".join(f"# degraded: {d}\n" for d in v["degradations"])
    text = head + "".join(item["text"] for item in v["items"])
    if arm == "C":
        return text
    if arm == "E":
        sep = "" if text.endswith("\n") else "\n"
        end = f"# end of oxide context: {len(v['items'])} items\n"
        return f"# {ENDS.format('context')}\n{text}{sep}{end}"
    raise ValueError(arm)


def _self_check() -> None:
    v = {"snapshot": {"s": 1}, "query": "q", "entries": [{"a": 1}, {"a": 2}],
         "lexical": {"truncated": True}, "hints": []}  # fmt: skip
    assert json.loads(search_text(v, "CL")) == v
    assert list(json.loads(search_text(v, "CL"))) == list(v)
    assert json.loads(search_text(v, "CM")) == counted(v)
    assert list(json.loads(search_text(v, "CM")))[:2] == ["entries_returned", "more_available"]
    assert search_text(v, "E").splitlines()[1] == search_text(v, "C")
    b = {"used": 1, "budget": 2, "units": "u", "items": [{"text": "### a:1-1 x\nx\n"}],
         "omissions": [], "snapshot": {"snapshot_id": "s"}, "degradations": []}  # fmt: skip
    assert context_text(b, "C") in context_text(b, "E")


_self_check()
