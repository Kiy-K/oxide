import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text())


def normalized_pair(new_dir, old_dir, result_name):
    new, old = Path(new_dir), Path(old_dir)
    a, b = load(new / result_name), load(old / result_name)
    if result_name == "results.json":
        av, bv = a["versions"], b["versions"]
        counts = {"versions.derivation": int(av.get("derivation") != bv.get("derivation"))}
        av["derivation"] = bv["derivation"]
    else:
        counts = {}
    assert a == b, f"{new / result_name} differs outside normalized fields"
    ma, mb = load(new / "manifest.json"), load(old / "manifest.json")
    allowed = {"baseline", "protocol", "oxide_source", "derivation"}
    changes = {k: (ma.get(k), mb.get(k)) for k in set(ma) | set(mb) if ma.get(k) != mb.get(k)}
    assert set(changes) <= allowed, f"manifest unexpected differences: {changes}"
    assert all(ma.get(k) != mb.get(k) for k in allowed), f"manifest expected fields unchanged: {changes}"
    assert load(new / "metrics.json") == load(old / "metrics.json")
    print(f"{new}: {result_name} normalized {counts}; manifest normalized {sorted(changes)}; metrics identical")
    print(f"derivation new={ma['derivation']} old={mb['derivation']}")


normalized_pair("docs/phase-6/baseline-v15", "docs/phase-6/baseline-v14", "results.json")
normalized_pair("docs/phase-6/typescript-v12", "docs/phase-6/typescript-v11", "results.json")
new, old = load("docs/phase-6/typescript-v12/performance.json"), load("docs/phase-6/typescript-v11/performance.json")
print("performance keys", sorted(new))
print("new performance", new)
print("old performance", old)
