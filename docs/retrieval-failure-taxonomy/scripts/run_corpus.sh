#!/bin/bash
# usage: run_corpus.sh <corpus-name>   (reads inputs/corpora.json)
set -u
W=/tmp/claude-0/work; C=$1
read repo commit < <(python3 -c "import json;v=json.load(open('$W/inputs/corpora.json'))['$C'];print(v[0],v[1])")
D=$W/corp/$C
if [ ! -f $D/.oxide/index.db ]; then
  if [ ! -d $D ]; then
    git -C $W/src/$repo cat-file -e $commit^{commit} 2>/dev/null || git -C $W/src/$repo fetch -q origin $commit
    git -C $W/src/$repo worktree add -q --detach $D $commit || exit 3
  fi
  (cd $D && /home/user/oxide/target/release/oxide index . > $W/logs/$C.index.log 2>&1) || { echo "INDEX FAIL $C"; exit 4; }
fi
for f in $W/inputs/*__$C.jsonl; do
  b=$(basename $f .jsonl)
  $W/tgt/release/examples/taxonomy_dump $D $f > $W/out/$b.jsonl 2> $W/logs/$b.dump.log || echo "DUMP FAIL $b"
done
echo "done $C"
