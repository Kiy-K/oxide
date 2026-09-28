cd /tmp/claude-0/-home-user-oxide/addc97ee-9d65-5c7d-8bef-c04e3113af55/scratchpad/r1/work/src
for r in pylint-dev/pylint pytest-dev/pytest colinhacks/zod pallets/flask BurntSushi/ripgrep encode/httpx coder/code-server darkreader/darkreader mwaskom/seaborn EuniAI/ContextBench; do n=${r#*/}; ( git clone -q https://github.com/$r $n > ../logs/clone-$n.log 2>&1; echo "$n $?" >> ../logs/clones.txt ) & done
wait; echo ALLDONE >> ../logs/clones.txt
