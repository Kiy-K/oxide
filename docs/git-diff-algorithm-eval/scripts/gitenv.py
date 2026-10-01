import os, subprocess
S = os.getcwd()
EMPTY = os.path.join(S, "emptyhome")  # empty HOME/XDG_CONFIG_HOME: no ~/.gitconfig
os.makedirs(EMPTY, exist_ok=True)
ALGOS = {
    "default": [],
    "myers": [("diff.algorithm", "myers")],
    "minimal": [("diff.algorithm", "minimal")],
    "histogram": [("diff.algorithm", "histogram")],
    "patience": [("diff.algorithm", "patience")],
    # not an algorithm, but another host knob that moves hunk boundaries
    "myers_noindent": [("diff.algorithm", "myers"), ("diff.indentHeuristic", "false")],
}
def env_for(algo):
    e = {k: v for k, v in os.environ.items()
         if not k.startswith("GIT_") and k not in ("HOME", "XDG_CONFIG_HOME")}
    e.update(HOME=EMPTY, XDG_CONFIG_HOME=EMPTY, GIT_CONFIG_NOSYSTEM="1",
             GIT_CONFIG_GLOBAL="/dev/null", LC_ALL="C", TZ="UTC")
    kv = ALGOS[algo]
    e["GIT_CONFIG_COUNT"] = str(len(kv))
    for i, (k, v) in enumerate(kv):
        e[f"GIT_CONFIG_KEY_{i}"] = k; e[f"GIT_CONFIG_VALUE_{i}"] = v
    return e
# exact argv of gitutil::diff_text (range form / worktree form)
def diff_argv(rng):
    a = ["git", "diff", "--unified=0", "--no-color", "--src-prefix=a/", "--dst-prefix=b/"]
    return a + ([rng] if rng else ["HEAD"])
def rust_lines(text):
    out = text.split("\n")
    if out and out[-1] == "": out.pop()
    return [l[:-1] if l.endswith("\r") else l for l in out]
def parse_unified(text):
    """Line-for-line port of gitutil::parse_unified + parse_hunk_header."""
    files, cur = {}, None
    for line in rust_lines(text):
        if line.startswith("+++ /dev/null"): cur = None
        elif line.startswith("+++ b/"): cur = line[6:].strip()
        elif line.startswith("@@"):
            if cur is None: continue
            h = parse_hunk(line)
            if h is None: continue
            ns, nc = h
            ent = files.setdefault(cur, [])
            if nc > 0: ent.append((max(ns,1), max(ns+nc-1,1)))
            else:
                p = max(ns,1); ent.append((max(p-1,1), p+1))
    return {k: files[k] for k in sorted(files)}
def parse_hunk(line):
    ws = line.split()
    if len(ws) < 3 or not ws[2].startswith("+"): return None
    plus = ws[2][1:]
    try:
        if "," in plus:
            s, c = plus.split(",", 1); s = int(s); c = int(c.rstrip("@").strip())
        else: s, c = int(plus), 1
    except ValueError: return None
    if s < 0 or c < 0: return None
    return max(s,1), c
