#!/usr/bin/env bash
# Run score.py under the PROBE.md §5 caps, with a watchdog that aborts on host stress.
# usage: JULIA=<Julia-1 checkout> PY=<python with torch+transformers> run.sh <timeout_s> score.py args...
set -u
timeout_s=$1; shift
here=$(cd "$(dirname "$0")" && pwd)
unit=typed-evidence-$$
swap0=$(free -m | awk '/Swap/{print $3}')
THREADS=2 JULIA_CPU_THREADS=2 HF_HUB_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$JULIA:$here" \
  timeout "$timeout_s" systemd-run --user --scope -q --unit="$unit" \
  -p MemoryMax=2560M -p MemorySwapMax=0 -p CPUQuota=200% \
  nice -n 10 unshare -rn "$PY" "$here/$1" "${@:2}" &
pid=$!
while kill -0 "$pid" 2>/dev/null; do
  read -r avail swap < <(free -m | awk '/Mem/{a=$7} /Swap/{s=$3} END{print a, s}')
  if [ "$avail" -lt 1500 ] || [ $((swap - swap0)) -gt 200 ]; then
    echo "ABORT: host stress (available ${avail} MB, swap +$((swap - swap0)) MB)" >&2
    systemctl --user stop "$unit.scope"
  fi
  sleep 2
done
wait "$pid"; rc=$?
journalctl --user --no-pager -q -u "$unit.scope" | grep -E "Consumed|oom|Failed" >&2
exit $rc
