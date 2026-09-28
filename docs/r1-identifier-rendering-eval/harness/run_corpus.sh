#!/bin/bash
# run_corpus.sh <corpus> <full|d0only> [parb]
set -u
W=/tmp/claude-0/-home-user-oxide/addc97ee-9d65-5c7d-8bef-c04e3113af55/scratchpad/r1/work
B=$W/tgt/release; SV=$B/examples/semantic_variant
C=$1; MODE=$2; PARB=${3:-}
D=$W/corp/$C
unset OXIDE_EMBED_URL OXIDE_EMBED_MODEL OXIDE_EMBED_NATIVE OXIDE_EMBED_SESSIONS
export PYTHONDONTWRITEBYTECODE=1
if [ ! -f $D/.oxide/index.db ]; then
  s=$(date +%s%N)
  (cd $D && $B/oxide index .) > $W/logs/idx-$C.log 2>&1 || { echo "INDEX FAIL $C"; exit 4; }
  echo "{\"corpus\":\"$C\",\"index_ms\":$(( ($(date +%s%N)-s)/1000000 ))}" >> $W/logs/index_time.jsonl
fi
[ "${INDEX_ONLY:-}" = 1 ] && { echo "indexed $C"; exit 0; }
cat $W/tasks/*__$C.jsonl > $W/tmp/$C.tasks
copy(){ python3 -c "import sqlite3,sys,os
os.path.exists(sys.argv[2]) and os.remove(sys.argv[2])
sqlite3.connect(sys.argv[1]).execute(\"VACUUM INTO '\"+sys.argv[2]+\"'\")" $D/.oxide/index.db $1; }
copy $W/db/$C.d0.db
$SV $D $W/tmp/$C.tasks --db $W/db/$C.d0.db --variant D0 --no-embed > $W/dumps/D0/$C.jsonl 2> $W/logs/d0-$C.log || echo "D0 FAIL $C"
$SV $D $W/tmp/$C.tasks --db $W/db/$C.d0.db --variant D0 --dump-texts 1000000000 > $W/texts/D0/$C.jsonl 2>/dev/null || echo "TXT0 FAIL $C"
$SV $D $W/tmp/$C.tasks --db $W/db/$C.d0.db --variant R1 --dump-texts 1000000000 > $W/texts/R1/$C.jsonl 2>/dev/null || echo "TXT1 FAIL $C"
rm -f $W/db/$C.d0.db
if [ "$MODE" = full ]; then
  copy $W/db/$C.r1.db
  $SV $D $W/tmp/$C.tasks --db $W/db/$C.r1.db --variant R1 --cache $W/cache/r1.db > $W/dumps/R1/$C.jsonl 2> $W/logs/r1-$C.log || echo "R1 FAIL $C"
  [ -n "$PARB" ] || rm -f $W/db/$C.r1.db
fi
if [ -n "$PARB" ]; then
  copy $W/db/$C.d0re.db
  $SV $D $W/tmp/$C.tasks --db $W/db/$C.d0re.db --variant D0 --cache $W/cache/d0.db > $W/dumps/D0re/$C.jsonl 2> $W/logs/d0re-$C.log || echo "D0RE FAIL $C"
fi
echo "done $C"
