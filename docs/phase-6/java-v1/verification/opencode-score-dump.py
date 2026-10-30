# OpenCode's docs/phase-6/java-v1-opencode/oracle/score.py, plus dumps of its ambiguous misses and no-oracle edges.
# Usage: uv run --locked python opencode-score-dump.py GSON_ROOT ../../java-v1-opencode/oracle/gson-uses.json OUT_JSON
import json
import hashlib
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from oxide.capture import capture
from oxide.core.indexing import derive

ROOT = Path(sys.argv[1]).resolve()
ORACLE = Path(sys.argv[2])
OUT = Path(sys.argv[3])
IDENT = re.compile(rb"[A-Za-z_$][A-Za-z0-9_$]*")


def char_offset(data: bytes, offset: int) -> int:
    return len(data[:offset].decode("utf-8").encode("utf-16-le")) // 2


def use_offset(data: bytes, start: int, end: int) -> int | None:
    text = data[start:end]
    if b"<" in text:
        text = text[:text.index(b"<")]
    matches = list(IDENT.finditer(text))
    return char_offset(data, start + matches[-1].start()) if matches else None


snap = capture(ROOT)
derived = derive(snap, "gson-oracle")
data = {f.path: f.data for f in snap.files}
entities = {e.key: e for e in derived.entities}
uses = json.loads(ORACLE.read_text())["uses"]
file_modules = defaultdict(set)
for edge in derived.edges:
    if edge.kind == "contains_logical" and edge.targets:
        file_modules[edge.source].add(edge.targets[0])

stats = {k: Counter() for k in ("calls", "references", "imports")}
no_oracle = Counter()
disagreements = []
ambiguous = Counter()
unresolved = defaultdict(list)
MISS = []
NO = []


def declaration_matches(target: str, decl: str) -> bool:
    path, offset = decl.rsplit(":", 1)
    offset = int(offset)
    entity = entities.get(target)
    if entity and entity.source:
        src = entity.source
        return path == src.path and char_offset(data[src.path], src.start) <= offset < char_offset(data[src.path], src.end)
    if entity and entity.category == "module":
        return path in {t.removeprefix("file:") for t in file_modules.get(target, ())}
    return False


def unresolved_cause(edge, text: str) -> str:
    compact = "".join(text.split())
    if ")." in compact or "] .".replace(" ", "") in compact:
        return "chained call"
    if len(re.findall(r"[A-Za-z_$][\w$]*", compact)) >= 3 and "." in compact:
        return "field of a variable"
    return "other"

for edge in derived.edges:
    kind = edge.kind
    at = edge.evidence
    if kind not in stats or at is None or not at.path.endswith(".java"):
        continue
    off = use_offset(data[at.path], at.start, at.end)
    oracle_decl = uses.get(f"{at.path}:{off}") if off is not None else None
    if edge.resolution == "ambiguous":
        matched = bool(oracle_decl and any(declaration_matches(t, oracle_decl) for t in edge.targets))
        ambiguous[kind + ("_hit" if matched else "_miss")] += 1
        if not matched:
            line=data[at.path][:at.start].count(b"\n")+1
            MISS.append((at.path, line, data[at.path][at.start:at.end].decode(), off, oracle_decl, [t.rsplit("#",1)[-1] for t in edge.targets]))
        continue
    if edge.resolution == "unresolved":
        if kind in ("calls", "references") and oracle_decl:
            text=data[at.path][at.start:at.end].decode("utf-8","replace")
            unresolved[kind].append((hashlib.sha256(repr(edge).encode()).hexdigest(),edge,oracle_decl,text,unresolved_cause(edge,text)))
        continue
    if not oracle_decl:
        no_oracle[kind] += 1
        t=data[at.path][at.start:at.end].decode()
        NO.append((kind, at.path.split("/")[0], "static" if t.startswith("import static") else ("<" if "<" in t else "new" if t.startswith("new") else "other"), t[:60]))
        continue
    decl_path, decl_char = oracle_decl.rsplit(":",1); decl_char=int(decl_char)
    agree=False
    for target in edge.targets:
        entity=entities.get(target)
        if entity and entity.source:
            src=entity.source; entity_data=data[src.path]
            a,b=char_offset(entity_data,src.start),char_offset(entity_data,src.end)
            if decl_path==src.path and a<=decl_char<b: agree=True
        elif entity and entity.category=="module":
            member_files={t.removeprefix("file:") for t in file_modules.get(target,())}
            if decl_path in member_files: agree=True
    if agree:
        stats[kind]["agree"]+=1
    else:
        stats[kind]["disagree"]+=1
        source=entities.get(edge.source)
        line=data[at.path][:at.start].count(b"\n")+1
        disagreements.append({"kind":kind,"source_entity":edge.source,"evidence":data[at.path][at.start:at.end].decode("utf-8","replace"),"path_line":f"{at.path}:{line}","oxide_targets":list(edge.targets),"javac_declaration":oracle_decl,"reason":"candidate span does not contain javac declaration"})

out={"counts":{k:{**dict(v),"precision":v["agree"]/(v["agree"]+v["disagree"]) if v["agree"]+v["disagree"] else None} for k,v in stats.items()},"overall":{"agree":sum(x["agree"] for x in stats.values()),"disagree":sum(x["disagree"] for x in stats.values())},"no_oracle_reasons":{"unrecorded_or_error_or_external":dict(no_oracle)},"ambiguous":dict(ambiguous),"disagreements":disagreements,"unresolved":{k:[{"evidence":text,"declaration":decl,"source":e.source,"cause":cause,"sha256":digest} for digest,e,decl,text,cause in sorted(v,key=lambda x:x[0])[:40]] for k,v in unresolved.items()}}
OUT.write_text(json.dumps(out,indent=2)+"\n")
print(json.dumps({"counts":out["counts"],"overall":out["overall"],"disagreements":len(disagreements),"ambiguous":dict(ambiguous),"no_oracle":dict(no_oracle)},indent=2))

import collections as C
print("misses", len(MISS), "with-type-args", sum("<" in m[2] for m in MISS), "oracle-none", sum(m[4] is None for m in MISS))
for m in MISS: print(m)

print("no-oracle", len(NO), C.Counter((k,m,c) for k,m,c,_ in NO).most_common())
for row in [r for r in NO if r[2]=="other"][:25]: print(row)
