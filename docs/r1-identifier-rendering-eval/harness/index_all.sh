cd /tmp/claude-0/-home-user-oxide/addc97ee-9d65-5c7d-8bef-c04e3113af55/scratchpad/r1/work
python3 -c "import json;print('\n'.join(json.load(open('corpora.json'))))" | INDEX_ONLY=1 xargs -P 2 -I{} ./run_corpus.sh {} full > logs/index_all.log 2>&1
echo INDEX_ALL_DONE >> logs/index_all.log
