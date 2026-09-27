#!/usr/bin/env python3
"""Research-only (issue #23): renders work/out/ into the summary tables.

Timing definitions (both engines, same process layout, same pinned core):
  OXIDE  = parse_file + compute_file_relations (the pipeline.rs seam)
  CG     = extract_file (kernel walk + buffer encode) + decode (buffers ->
           structs, the work any Rust consumer must add)
Warm = repetitions 2..N of each of the 3 processes; cold = repetition 1 of
each process (includes OXIDE's lazy query compilation and first-touch page
faults for both). Per-file statistic = median over all warm samples.
"""
import collections, glob, json, os, re, statistics, sys

out = sys.argv[1]


def load(pattern):
    return [json.load(open(p)) for p in sorted(glob.glob(os.path.join(out, pattern)))]


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))]


# work/out/ reads the live manifest one level up; results/ carries its own copy.
_m = os.path.join(out, "manifest.jsonl")
man = {x["id"]: x for x in map(json.loads, open(_m if os.path.exists(_m) else os.path.join(out, "../manifest.jsonl")))}
ox_runs, cg_runs = load("bench_oxide.*.json"), load("bench_cgk.*.json")


def per_file(runs, keys):
    """id -> (warm median ns, [cold ns per process])"""
    warm, cold = collections.defaultdict(list), collections.defaultdict(list)
    for run in runs:
        for f in run["files"]:
            t = [sum(v) for v in zip(*(f[k] for k in keys))]
            cold[f["id"]].append(t[0])
            warm[f["id"]].extend(t[1:])
    return {i: (statistics.median(warm[i]), cold[i]) for i in warm}


oxt = per_file(ox_runs, ["parse_ns", "relations_ns"])
oxp = per_file(ox_runs, ["parse_ns"])
cgt = per_file(cg_runs, ["extract_ns", "decode_ns"])
cge = per_file(cg_runs, ["extract_ns"])
ox1 = per_file(ox_runs, ["parse1_ns"])
cg1 = per_file(cg_runs, ["parse1_ns"])
cg_ok = {f["id"]: f["ok"] for f in cg_runs[0]["files"]}
nbytes = {f["id"]: f["bytes"] for f in ox_runs[0]["files"]}

print("# Differential run summary\n")
print(f"Reps per process: {ox_runs[0]['reps']} x {len(ox_runs)} processes per engine.\n")

print("## Corpus\n")
grid = collections.Counter((m["group"].split("/")[0], m["oxide_lang"]) for m in man.values())
langs = sorted({l for _, l in grid})
groups = sorted({g for g, _ in grid})
print("| group | " + " | ".join(langs) + " | total |")
print("|---|" + "---:|" * (len(langs) + 1))
for g in groups:
    row = [grid.get((g, l), 0) for l in langs]
    print(f"| {g} | " + " | ".join(str(x or "") for x in row) + f" | {sum(row)} |")
print()

print("## Determinism\n")
det = collections.defaultdict(set)
for line in open(os.path.join(out, "determinism.sha256")):
    h, p = line.split()
    det[os.path.basename(p).split(".")[0]].add(h)
for k, v in sorted(det.items()):
    print(f"- {k}: {len(v)} distinct output hash(es) across 3 processes")
print()

print("## Performance (per-language sum of per-file warm medians)\n")
print("| language | files | CG ok | KiB | OXIDE ms | CG ms | CG extract-only ms | OXIDE/CG | per-file OXIDE/CG p10 / median / p90 | OXIDE parse_file share | OXIDE seam ÷ 1 parse | CG extract ÷ 1 parse |")
print("|---|--:|--:|--:|--:|--:|--:|--:|---|--:|--:|--:|")
by_lang = collections.defaultdict(list)
for i, m in man.items():
    by_lang[m["oxide_lang"]].append(i)


def perf_row(name, ids):
    ok = [i for i in ids if cg_ok.get(i)]
    if not ok:
        print(f"| {name} | {len(ids)} | 0 | | | | | | kernel extracted no file | |")
        return
    o = sum(oxt[i][0] for i in ok) / 1e6
    c = sum(cgt[i][0] for i in ok) / 1e6
    ce = sum(cge[i][0] for i in ok) / 1e6
    kib = sum(nbytes[i] for i in ok) / 1024
    ratios = [oxt[i][0] / cgt[i][0] for i in ok if cgt[i][0] > 0]
    share = sum(oxp[i][0] for i in ok) / sum(oxt[i][0] for i in ok)
    # Parse-equivalents: how many bare tree-sitter parses (same grammar,
    # same file) each engine's per-file work costs.
    pe_o = o / (sum(ox1[i][0] for i in ok) / 1e6)
    pe_c = ce / (sum(cg1[i][0] for i in ok) / 1e6)
    print(f"| {name} | {len(ids)} | {len(ok)} | {kib:.0f} | {o:.1f} | {c:.1f} | {ce:.1f} | {o / c:.2f}x "
          f"| {pct(ratios, .1):.2f} / {statistics.median(ratios):.2f} / {pct(ratios, .9):.2f} | {share:.0%} | {pe_o:.1f} | {pe_c:.1f} |")


for l in sorted(by_lang):
    perf_row(l, by_lang[l])
perf_row("**all (CG-ok files)**", list(man))
print()
print("Only files the kernel extracted are timed on both sides (a deferred file "
      "returns early and would flatter the kernel). \"÷ 1 parse\" divides each engine's "
      "time by one bare tree-sitter parse of the same file with that engine's own grammar.\n")
cg_fail_ids = [i for i in man if not cg_ok.get(i) and man[i]["cg_lang"]]
if cg_fail_ids:
    print(f"Files the kernel deferred ({len(cg_fail_ids)}): OXIDE seam "
          f"{sum(oxt[i][0] for i in cg_fail_ids) / 1e6:.1f} ms total; kernel time-to-defer "
          f"{sum(cgt[i][0] for i in cg_fail_ids) / 1e6:.1f} ms total.\n")

print("## Cold vs warm (whole corpus, per process)\n")
print("| engine | cold rep ms (3 processes) | warm rep ms median | warm rep ms min–max |")
print("|---|---|--:|---|")
for name, runs, keys in [("OXIDE", ox_runs, ["parse_ns", "relations_ns"]),
                         ("CG kernel", cg_runs, ["extract_ns", "decode_ns"])]:
    # Rep totals are rebuilt from per-file engine time only, so the
    # attribution parse (parse1_ns) timed in the same loop never counts.
    totals = [[sum(sum(f[k][r] for k in keys) for f in run["files"]) / 1e6
               for r in range(run["reps"])] for run in runs]
    cold = [t[0] for t in totals]
    warm = [x for t in totals for x in t[1:]]
    print(f"| {name} | {', '.join(f'{c:.0f}' for c in cold)} | {statistics.median(warm):.0f} | {min(warm):.0f}–{max(warm):.0f} |")
print()
print("Rep totals are the sum of per-file engine time over every manifest file "
      "(the kernel's deferred files included, at their time-to-defer).\n")

print("## Memory\n")
print("| engine | VmHWM after corpus load (MiB) | VmHWM end of bench (MiB) | delta | /usr/bin/time max RSS, one dump (MiB) | wall, one dump (s) |")
print("|---|--:|--:|--:|--:|--:|")
for name, runs, tf in [("OXIDE", ox_runs, "time_oxide_dump.txt"), ("CG kernel", cg_runs, "time_cgk_dump.txt")]:
    a = statistics.median(r["vmhwm_kb_after_load"] for r in runs) / 1024
    b = statistics.median(r["vmhwm_kb_end"] for r in runs) / 1024
    t = open(os.path.join(out, tf)).read()
    rss = int(re.search(r"Maximum resident set size \(kbytes\): (\d+)", t).group(1)) / 1024
    wall = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): ([\d:.]+)", t).group(1)
    mm, ss = wall.split(":")[-2:]
    print(f"| {name} | {a:.1f} | {b:.1f} | {b - a:+.1f} | {rss:.1f} | {int(mm) * 60 + float(ss):.2f} |")
print()


def qtable(path, title):
    d = json.load(open(os.path.join(out, path)))
    print(f"## {title}\n")
    print("| language | files | compared | CG failed | OXIDE syms on CG-failed | OXIDE syms | matched | exact span | kind agree | qn exact / suffix / differs | OXIDE-only | CG-only (comparable kinds) | CG-only (schema-extra) | CG namespace |")
    print("|---|--:|--:|--:|--:|--:|--:|--:|--:|---|--:|--:|--:|--:|")
    for L, a in list(d["by_language"].items()) + [("**total**", d["total"])]:
        g = lambda k: a.get(k, 0)
        matched = g("match_T1") + g("match_T2") + g("match_T3") + g("match_T4")
        print(f"| {L} | {g('files')} | {g('compared')} | {g('cg_failed')} | {g('oxide_symbols_on_cg_failed')} | {g('oxide_symbols')} "
              f"| {matched} | {g('match_T1')} | {g('kind_agree')} | {g('qn_exact')} / {g('qn_suffix')} / {g('qn_differs')} "
              f"| {g('oxide_only')} | {g('cg_only')} | {g('cg_only_schema_extra')} | {g('cg_only_namespace')} |")
    print()
    print("| language | imports OXIDE | exact | OXIDE-only | CG-only | calls OXIDE | exact | OXIDE-only | CG-only (calls) | CG-only (instantiates) | callee names exact / OXIDE | bases OXIDE | exact | OXIDE-only | CG-only | CG non-identifier rel. names |")
    print("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|--:|--:|--:|--:|--:|")
    for L, a in list(d["by_language"].items()) + [("**total**", d["total"])]:
        g = lambda k: a.get(k, 0)
        ni = sum(v for k, v in a.items() if k.startswith("cg_rel_non_identifier_"))
        print(f"| {L} | {g('imports_oxide')} | {g('imports_exact')} | {g('imports_oxide_only')} | {g('imports_cg_only')} "
              f"| {g('calls_oxide')} | {g('calls_exact')} | {g('calls_oxide_only')} | {g('calls_cg_only_from_calls')} | {g('calls_cg_only_from_instantiates')} "
              f"| {g('callee_names_exact')} / {g('callee_names_oxide')} | {g('bases_oxide')} | {g('bases_exact')} | {g('bases_oxide_only')} | {g('bases_cg_only')} | {ni} |")
    print()


qtable("diff.json", "Output differences — raw input (what a Rust-only integration would see)")
qtable("diff_preparsed.json", "Output differences — C/C++ after CodeGraph's own TS preParse (sensitivity)")
