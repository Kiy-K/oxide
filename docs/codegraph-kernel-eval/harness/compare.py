#!/usr/bin/env python3
"""Research-only (issue #23): normalizes and diffs OXIDE vs CodeGraph Kernel
per-file extraction dumps. Every rule below is the documented normalization
(see ../README.md §Normalization); nothing else is applied.

  compare.py <manifest.jsonl> <oxide.jsonl> <cgk.jsonl> <out.json>

Scope: only files both sides extracted are diffed; files either side failed
are counted separately, never dropped. OXIDE's synthetic `<file>:__module__`
symbol and CodeGraph's `file` node are excluded from symbol matching (both
are whole-file stand-ins) but remain the attribution target for top-level
calls on each side.
"""
import collections, json, re, sys

# --- normalization tables -------------------------------------------------

# N1. Kind classes. OXIDE's 9 kinds vs CodeGraph's 23; only a class-level
# agreement is meaningful. `struct`/`union` count as class (OXIDE's Rust/Go/C
# struct -> Class), `trait`/`protocol` as interface, `namespace` as module.
KIND_CLASS = {
    "class": "class", "struct": "class", "union": "class",
    "interface": "interface", "trait": "interface", "protocol": "interface",
    "function": "function", "method": "method",
    "enum": "enum", "type_alias": "type_alias",
    "constant": "constant", "variable": "variable",
    "module": "module", "namespace": "module",
    "field": "field", "property": "property", "enum_member": "enum_member",
    "route": "route", "component": "component",
}
# CodeGraph node kinds never considered as definitions for matching.
CG_EXCLUDED = {"file", "import", "export", "parameter"}
# CodeGraph kinds OXIDE's schema has no counterpart for at all. Unmatched
# nodes of these kinds are reported as "schema-extra", not as definitions
# OXIDE missed. (`variable` stays matchable: OXIDE emits some of those as
# `constant`.)
SCHEMA_EXTRA = {"variable", "field", "property", "enum_member", "route", "component"}

SIG_SUFFIX = re.compile(r"\(.*$")          # N3: Java/C++ `(params)[const]` suffix


def oxide_qn(qn):
    # N3. OXIDE separates with '.', and Java/C++ append a normalized signature
    # to the last segment; strip the signature.
    return SIG_SUFFIX.sub("", qn)


def cg_qn(qn):
    # N3. CodeGraph separates with '::'.
    return (qn or "").replace("::", ".")


def qn_category(o, c):
    if o == c:
        return "exact"
    # N4. CodeGraph prefixes Java packages / PHP namespaces that OXIDE does
    # not qualify with (and vice versa for declared C++/Rust modules OXIDE
    # counts as containers): one being a dotted suffix of the other.
    if c.endswith("." + o) or o.endswith("." + c):
        return "suffix"
    return "differs"


def bare(name):
    # N5. Relation targets: last segment after '.', '::', '->', '\', generics
    # and call parens stripped. OXIDE's queries already report bare names;
    # CodeGraph reports the written receiver path (`strings.Join`, `ns.Base`).
    n = re.sub(r"<.*?>", "", name or "")
    n = re.split(r"::|->|\.|\\", n)[-1]
    n = n.split("(")[0].strip()
    return n


def strip_import(s, lang):
    # N6. Imports compare as raw module strings; only quoting is removed.
    s = s.strip().strip("'\"<>")
    # N6b. OXIDE rewrites a quoted C/C++ include to `./x.h` (relative marker,
    # tags.rs `c_includes_are_relative_when_quoted...`); CodeGraph keeps `x.h`.
    if lang in ("c", "cpp") and s.startswith("./"):
        s = s[2:]
    return s


def expand_use_tree(path):
    """N6d. OXIDE keeps a grouped Rust `use` verbatim (`std::{io, path::Path}`);
    CodeGraph emits one leaf path per item. Expand OXIDE's side the same way
    (`self` -> the group's prefix, `as` aliases dropped)."""
    path = re.sub(r"\s+as\s+[A-Za-z_]\w*", "", path)
    path = re.sub(r"\s+", "", path)
    if "{" not in path:
        return [path]
    i = path.index("{")
    prefix, body = path[:i], path[i + 1:path.rindex("}")]
    parts, depth, cur = [], 0, ""
    for ch in body:
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        depth += ch == "{"
        depth -= ch == "}"
        cur += ch
    parts.append(cur)
    out = []
    for part in filter(None, parts):
        if part == "self":
            out.append(prefix.rstrip(":"))
        else:
            out.extend(expand_use_tree(prefix + part))
    return out


IDENT = re.compile(r"^[A-Za-z_$][\w$]*[!?]?$")


# --- matching -------------------------------------------------------------


def match_symbols(osyms, cnodes):
    """One-to-one greedy matching on bare name, in tiers:
    T1 same start+end, T2 same end, T3 same start, T4 overlapping span.
    Ties prefer equal kind class, then the smallest line distance."""
    cands = []
    by_name = collections.defaultdict(list)
    for j, c in enumerate(cnodes):
        by_name[c["name"]].append(j)
    for i, o in enumerate(osyms):
        for j in by_name.get(o["name"], []):
            c = cnodes[j]
            if o["start"] == c["start"] and o["end"] == c["end"]:
                tier = 1
            elif o["end"] == c["end"]:
                tier = 2
            elif o["start"] == c["start"]:
                tier = 3
            elif o["start"] <= c["end"] and c["start"] <= o["end"]:
                tier = 4
            else:
                continue
            kind_diff = KIND_CLASS.get(o["kind"]) != KIND_CLASS.get(c["kind"])
            dist = abs(o["start"] - c["start"]) + abs(o["end"] - c["end"])
            cands.append((tier, kind_diff, dist, i, j))
    cands.sort()
    used_o, used_c, pairs = set(), set(), []
    for tier, _, _, i, j in cands:
        if i in used_o or j in used_c:
            continue
        used_o.add(i)
        used_c.add(j)
        pairs.append((i, j, tier))
    return pairs


# --- per-file diff ----------------------------------------------------------


def diff_file(ox, cg, acc):
    osyms = [s for s in ox["symbols"] if not s["qn"].endswith(":__module__")]
    module = next(s for s in ox["symbols"] if s["qn"].endswith(":__module__"))
    rows = cg["nodes"]
    cand_rows = [r for r, n in enumerate(rows) if n["kind"] not in CG_EXCLUDED]
    cnodes = [rows[r] for r in cand_rows]
    pairs = match_symbols(osyms, cnodes)

    acc["oxide_symbols"] += len(osyms)
    acc["cg_nodes_considered"] += len(cnodes)
    row_owner = {0: module["qn"]}  # CG row -> OXIDE owner key
    matched_o, matched_c = set(), set()
    for i, j, tier in pairs:
        o, c = osyms[i], cnodes[j]
        matched_o.add(i)
        matched_c.add(j)
        row_owner[cand_rows[j]] = o["qn"]
        acc[f"match_T{tier}"] += 1
        ko, kc = KIND_CLASS.get(o["kind"]), KIND_CLASS.get(c["kind"])
        if ko == kc:
            acc["kind_agree"] += 1
        else:
            acc["kind_mismatch"] += 1
            acc["kind_mismatch_pairs"][f"{o['kind']}->{c['kind']}"] += 1
        acc[f"qn_{qn_category(oxide_qn(o['qn']), cg_qn(c['qn']))}"] += 1
    for i, o in enumerate(osyms):
        if i not in matched_o:
            acc["oxide_only"] += 1
            acc["oxide_only_kinds"][o["kind"]] += 1
            acc["examples_oxide_only"].append(f"{ox['id']}#{o['qn']}@{o['start']}")
    for j, c in enumerate(cnodes):
        if j not in matched_c:
            # N8. A `namespace` node OXIDE has no match for is a package /
            # namespace declaration (Java `package`, C++ `namespace` blocks
            # the kernel emits but OXIDE does not qualify with) - kept apart.
            if c["kind"] in SCHEMA_EXTRA:
                bucket = "cg_only_schema_extra"
            elif c["kind"] == "namespace":
                bucket = "cg_only_namespace"
            else:
                bucket = "cg_only"
            acc[bucket] += 1
            acc[bucket + "_kinds"][c["kind"]] += 1
            if bucket == "cg_only":
                acc["examples_cg_only"].append(f"{cg['id']}#{c['qn']}@{c['start']}")

    # Imports (N6).
    lang = ox["lang"]
    oimp = {strip_import(x, lang) for x in ox["imports"]}
    cimp_nodes = {strip_import(n["name"], lang) for n in rows if n["kind"] == "import"}
    cimp_refs = {strip_import(r["name"], lang) for r in cg["refs"] if r["kind"] == "imports"}
    if lang == "rust":
        # N6c. CodeGraph names a Rust `use` node after its first path segment
        # (`crate`, `std`) and puts the full path on the imports ref; OXIDE
        # records the full path. Compare against the refs' full paths.
        cimp_nodes = {x for x in cimp_refs if "::" in x}
        oimp = {leaf for x in oimp for leaf in expand_use_tree(x)}
    acc["imports_oxide"] += len(oimp)
    acc["imports_cg_nodes"] += len(cimp_nodes)
    acc["imports_exact"] += len(oimp & cimp_nodes)
    acc["imports_oxide_only"] += len(oimp - cimp_nodes)
    acc["imports_cg_only"] += len(cimp_nodes - oimp)
    acc["imports_oxide_found_anywhere"] += len(oimp & (cimp_nodes | cimp_refs))
    for x in sorted(oimp - cimp_nodes)[:3]:
        acc["examples_imports_oxide_only"].append(f"{ox['id']}:{x}")
    for x in sorted(cimp_nodes - oimp)[:3]:
        acc["examples_imports_cg_only"].append(f"{cg['id']}:{x}")

    # Calls and bases (N5, N7). OXIDE pairs: (owner qn, bare callee). CG
    # pairs: (OXIDE owner of the ref's source row, bare name); a source row
    # with no OXIDE counterpart keeps a distinct `<cg:...>` owner key.
    def owner(frm):
        if isinstance(frm, int):
            if frm in row_owner:
                return row_owner[frm]
            return "<cg:" + cg_qn(rows[frm]["qn"]) + ">"
        acc["cg_refs_from_id_string"] += 1
        return "<cg-id:" + str(frm) + ">"

    ocalls = {(s["qn"], c) for s in ox["symbols"] for c in s["calls"]}
    obases = {(s["qn"], b) for s in ox["symbols"] for b in s["bases"]}
    ccalls, cinst, cbases = set(), set(), set()
    for r in cg["refs"]:
        if r["kind"] in ("calls", "instantiates", "extends", "implements") and not IDENT.match(bare(r["name"])):
            # N5b. CodeGraph sometimes records raw expression text as a callee
            # (`self.view_functions[rule.endpoint]`, IIFEs, `func` literals).
            # OXIDE only ever reports identifiers; these are counted apart.
            acc["cg_rel_non_identifier_" + r["kind"]] += 1
            continue
        if r["kind"] == "calls":
            ccalls.add((owner(r["from"]), bare(r["name"])))
        elif r["kind"] == "instantiates":
            cinst.add((owner(r["from"]), bare(r["name"])))
        elif r["kind"] in ("extends", "implements"):
            cbases.add((owner(r["from"]), bare(r["name"])))
    # N7. OXIDE's callers queries count constructions (`new X`, `X.new`) as
    # calls; CodeGraph splits them into `instantiates`. Primary comparison
    # unions them; the CG-only residue is reported by source kind so the
    # split stays visible.
    call_union = ccalls | cinst
    acc["calls_oxide"] += len(ocalls)
    acc["calls_cg_calls"] += len(ccalls)
    acc["calls_cg_instantiates"] += len(cinst)
    acc["calls_exact"] += len(ocalls & call_union)
    acc["calls_oxide_only"] += len(ocalls - call_union)
    acc["calls_cg_only_from_calls"] += len(ccalls - ocalls)
    acc["calls_cg_only_from_instantiates"] += len((cinst - ccalls) - ocalls)
    # Attribution-free view: which bare callee names each side sees at all.
    on, cn = {c for _, c in ocalls}, {c for _, c in call_union}
    acc["callee_names_oxide"] += len(on)
    acc["callee_names_exact"] += len(on & cn)
    acc["callee_names_oxide_only"] += len(on - cn)
    acc["callee_names_cg_only"] += len(cn - on)
    for x in sorted(ocalls - call_union)[:2]:
        acc["examples_calls_oxide_only"].append(f"{ox['id']}:{x}")
    for x in sorted(ccalls - ocalls)[:2]:
        acc["examples_calls_cg_only"].append(f"{cg['id']}:{x}")

    acc["bases_oxide"] += len(obases)
    acc["bases_cg"] += len(cbases)
    acc["bases_exact"] += len(obases & cbases)
    acc["bases_oxide_only"] += len(obases - cbases)
    acc["bases_cg_only"] += len(cbases - obases)
    for x in sorted(obases - cbases)[:3]:
        acc["examples_bases_oxide_only"].append(f"{ox['id']}:{x}")
    for x in sorted(cbases - obases)[:3]:
        acc["examples_bases_cg_only"].append(f"{cg['id']}:{x}")

    # CodeGraph output with no OXIDE counterpart at all (reported, not scored).
    for r in cg["refs"]:
        if r["kind"] not in ("calls", "instantiates", "extends", "implements", "imports"):
            acc["cg_extra_refs"][r["kind"]] += 1


COUNTERS = ["kind_mismatch_pairs", "oxide_only_kinds", "cg_only_kinds",
            "cg_only_schema_extra_kinds", "cg_only_namespace_kinds", "cg_extra_refs"]
EXAMPLES = ["examples_oxide_only", "examples_cg_only", "examples_imports_oxide_only",
            "examples_imports_cg_only", "examples_calls_oxide_only", "examples_calls_cg_only",
            "examples_bases_oxide_only", "examples_bases_cg_only"]


def new_acc():
    acc = collections.defaultdict(int)
    for k in COUNTERS:
        acc[k] = collections.Counter()
    for k in EXAMPLES:
        acc[k] = []
    return acc


def plain(acc, n_examples=8):
    r = {}
    for k, v in sorted(acc.items()):
        if isinstance(v, collections.Counter):
            r[k] = dict(v.most_common(12))
        elif isinstance(v, list):
            r[k] = v[:n_examples]
        else:
            r[k] = v
    return r


def main():
    manifest, ox_path, cg_path, out = sys.argv[1:5]
    man = {x["id"]: x for x in map(json.loads, open(manifest))}
    ox = {x["id"]: x for x in map(json.loads, open(ox_path))}
    cg = {x["id"]: x for x in map(json.loads, open(cg_path))}
    assert ox.keys() == cg.keys() == man.keys(), "dumps must cover the same manifest"
    by_lang = collections.defaultdict(new_acc)
    by_group = collections.defaultdict(new_acc)
    total = new_acc()
    for fid in man:
        o, c = ox[fid], cg[fid]
        for acc in (by_lang[o["lang"]], by_group[man[fid]["group"].split("/")[0]], total):
            acc["files"] += 1
            acc["oxide_tree_has_error"] += o["has_parse_error"]
            if not c["ok"]:
                acc["cg_failed"] += 1
                key = "cg_failed_unsupported" if c["error"].startswith("unsupported") else "cg_failed_defer"
                acc[key] += 1
                acc["oxide_symbols_on_cg_failed"] += sum(
                    1 for s in o["symbols"] if not s["qn"].endswith(":__module__"))
                continue
            acc["compared"] += 1
            diff_file(o, c, acc)
    json.dump({"by_language": {k: plain(v) for k, v in sorted(by_lang.items())},
               "by_group": {k: plain(v) for k, v in sorted(by_group.items())},
               "total": plain(total, 0)}, open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
