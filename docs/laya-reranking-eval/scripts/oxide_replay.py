"""Offline replica of `src/context.rs` allocation (issue #15 research only).

Takes the exact pre-allocation `kept` pool production dumps via
`OXIDE_DEBUG_DUMP_KEPT` and replays role ordering -> relevance floor ->
per-file / primary / test caps -> shrink-to-fit greedy fill, with
`render_snippet` / `estimate_tokens` reproduced byte-for-byte (Rust `len()` is
UTF-8 bytes, `chars().count()` is code points; `str::lines` semantics).
`evaluate.py parity` checks it reproduces every baseline pack before any
challenger pack is trusted (results/baseline/parity.json). Stdlib only.
"""
import math
import struct

CHARS_PER_TOKEN = 4.0
ITEM_OVERHEAD = 12
PER_ITEM_CAP = 350
FLOOR_FRACTION = 0.15
MAX_PER_FILE = 2
MAX_PRIMARIES = 5
MAX_TESTS = 1
BUDGET = 4096
ROLE_RANK = {"primary": 0, "dependency": 1, "test": 2}


def f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


def blen(s):
    return len(s.encode("utf-8"))


def rust_lines(text):
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return [p[:-1] if p.endswith("\r") else p for p in parts]


def query_terms(task):
    out, cur = [], []
    for ch in task.lower() + " ":
        if ch.isalnum():
            cur.append(ch)
            continue
        w = "".join(cur)
        cur = []
        if blen(w) >= 3 and w not in out:
            out.append(w)
    return out


def estimate_tokens(text):
    return math.ceil(f32(len(text) / CHARS_PER_TOKEN))


_SRC = {}


def read_source(root, rel):
    key = (root, rel)
    if key not in _SRC:
        try:
            with open(f"{root}/{rel}", "rb") as fh:
                _SRC[key] = fh.read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            _SRC[key] = None
    return _SRC[key]


def window(lines, start_line, end_line, terms, max_tokens):
    """`render_snippet` over an already-split file."""
    lo = max(start_line - 1, 0)
    hi = min(end_line, len(lines))
    if hi <= lo:
        return ""
    body = lines[lo:hi]
    budget = int(f32(max_tokens * CHARS_PER_TOKEN))
    total = sum(blen(l) + 1 for l in body)
    if total <= budget:
        return "\n".join(body)
    scores = [sum(1 for t in terms if t in l.lower()) for l in body]
    best = max(scores) if scores else 0
    if best == 0 or budget == 0:
        out = ""
        for l in body:
            if blen(out) + blen(l) + 1 > budget:
                break
            out += l + "\n"
        return out.rstrip("\n")
    lo_i = scores.index(best)
    hi_i = lo_i
    used = blen(body[lo_i]) + 1
    while True:
        can_lo = lo_i > 0
        can_hi = hi_i + 1 < len(body)
        if not can_lo and not can_hi:
            break
        grow_lo = can_lo and (not can_hi or blen(body[lo_i - 1]) <= blen(body[hi_i + 1]))
        cost = (blen(body[lo_i - 1]) if grow_lo else blen(body[hi_i + 1])) + 1
        if used + cost > budget:
            break
        if grow_lo:
            lo_i -= 1
        else:
            hi_i += 1
        used += cost
    return "\n".join(body[lo_i:hi_i + 1])


def render_snippet(root, sym, terms, max_tokens):
    src = read_source(root, sym["file"])
    if src is None:
        return ""
    return window(rust_lines(src), sym["start_line"], sym["end_line"], terms, max_tokens)


def sid(c):
    return f"{c['symbol']['file']}#{c['symbol']['qualified_name']}"


def alloc_order(kept, ids):
    """Production's pre-fill order: role, score desc, numeric symbol id."""
    return sorted(kept, key=lambda c: (ROLE_RANK[c["role"]], -c["score"], ids[sid(c)]))


def allocate(root, task, kept, top_seed_score, ids, budget=BUDGET):
    """Replay allocation on an already-deduped `kept` pool. Returns
    (items [(sid, role, est_tokens)], used_tokens)."""
    kept = alloc_order(kept, ids)
    floor = f32(f32(top_seed_score) * f32(FLOOR_FRACTION))
    strong = [c for c in kept if f32(c["score"]) >= floor]
    if strong:
        kept = strong
    terms = query_terms(task)
    top = sid(kept[0]) if kept else None
    per_file, primaries, tests, used, items = {}, 0, 0, 0, []
    for c in kept:
        s, f = sid(c), c["symbol"]["file"]
        if per_file.get(f, 0) >= MAX_PER_FILE and s != top:
            continue
        if c["role"] == "primary":
            if primaries >= MAX_PRIMARIES:
                continue
            primaries += 1
        elif c["role"] == "test":
            if tests >= MAX_TESTS:
                continue
            tests += 1
        cap = min(PER_ITEM_CAP, budget)
        while True:
            snip = render_snippet(root, c["symbol"], terms, cap)
            est = estimate_tokens(snip) + ITEM_OVERHEAD
            if used + est <= budget or cap == 0:
                break
            cap //= 2
        if used + est > budget:
            if c["role"] == "primary":
                primaries -= 1
            elif c["role"] == "test":
                tests -= 1
            continue
        used += est
        per_file[f] = per_file.get(f, 0) + 1
        items.append((s, c["role"], est))
    return items, used


def transplant(kept, ids, new_order_sids, top_n=20):
    """Reorder the first `top_n` of production's allocation order to
    `new_order_sids`, transplanting scores rank-for-rank (RET-005: the score
    multiset, hence the floor's and caps' view, is unchanged). Roles, reasons
    and provenance stay with their own candidate; nothing is removed."""
    base = alloc_order(kept, ids)
    head, tail = base[:top_n], base[top_n:]
    by_sid = {sid(c): c for c in head}
    assert sorted(new_order_sids) == sorted(by_sid), "reorder must permute the shortlist exactly"
    scores = sorted((c["score"] for c in head), reverse=True)
    out = []
    for s, sc in zip(new_order_sids, scores):
        c = dict(by_sid[s])
        c["score"] = sc
        out.append(c)
    return out + tail


def symbol_id(file, qualified_name):
    """`Symbol::id`: FNV-1a 64 over length-prefixed [file, "\\0", qname]."""
    h = 0xCBF29CE484222325
    for part in (file.encode(), b"\0", qualified_name.encode()):
        for b in len(part).to_bytes(8, "little") + part:
            h ^= b
            h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def ids_for(kept):
    return {sid(c): symbol_id(c["symbol"]["file"], c["symbol"]["qualified_name"]) for c in kept}
