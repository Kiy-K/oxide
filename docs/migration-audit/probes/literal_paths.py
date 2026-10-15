"""Probe (migration audit, L5): do escaped quote/backslash paths round-trip through
LadybugDB literals in COPY, LOAD EXTENSION and RETURN? Run in a scratch directory:

    FTS=<libfts.lbug_extension> uv run python probes/literal_paths.py /tmp/scratch
"""
import os, sys, ladybug
base = sys.argv[1]
def esc(s): return s.replace("\\", "\\\\").replace("'", "\\'")
NAMES = ["o'brien", "back\\slash", "both'\\x", "trail\\", "uni\\u0041", "nl\\n",
         "raw\nnewline", "tab\there", "é ü", "dq\"x", "semi;x)", "dollar$x"]
for name in NAMES:
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    csv = os.path.join(d, "t.csv")
    open(csv, "w").write('"a"\n"b"\n')
    db = ladybug.Database(os.path.join(d, "g.lbug")); c = ladybug.Connection(db)
    c.execute("CREATE NODE TABLE T(k STRING PRIMARY KEY)")
    try:
        c.execute(f"COPY T FROM '{esc(csv)}' (header=false)")
        n = c.execute("MATCH (t:T) RETURN count(t)").get_all()[0][0]
        lit = c.execute(f"RETURN '{esc(csv)}'").get_all()[0][0]
        print(repr(name), "copy rows", n, "literal roundtrip", lit == csv)
    except Exception as e:
        print(repr(name), "ERR", type(e).__name__, str(e)[:150])
    ext = os.environ.get("FTS")
    if ext:
        link = os.path.join(d, "libfts.lbug_extension")
        if not os.path.exists(link): os.symlink(ext, link)
        try:
            c.execute(f"LOAD EXTENSION '{esc(link)}'"); print("  load ok")
        except Exception as e:
            print("  load ERR", str(e)[:150])
    c.close(); db.close()
