#!/bin/bash
# latency: --bench-batch 1 (production per-text path), no cache, D0 vs R1, alternating order
W=/tmp/claude-0/-home-user-oxide/addc97ee-9d65-5c7d-8bef-c04e3113af55/scratchpad/r1/work
SV=$W/tgt/release/examples/semantic_variant; OUT=$W/../results/bench.jsonl; : > $OUT
for repo in httpx zod ripgrep; do
  c=$(python3 -c "import json;print(next(n for n in json.load(open('$W/corpora.json')) if n.startswith('par-$repo@')))")
  for v in D0 R1 R1 D0; do
    $SV $W/corp/$c $W/tmp/$c.tasks --db $W/corp/$c/.oxide/index.db --variant $v --bench-batch 1 2>/dev/null \
      | python3 -c "import json,sys;d=json.loads(sys.stdin.read());d['corpus']='$c';print(json.dumps(d))" >> $OUT
  done
done
