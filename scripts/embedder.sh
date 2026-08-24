#!/usr/bin/env bash
# Managed llama.cpp embedding server for OXIDE.
#
# Usage:
#   scripts/embedder.sh start [--quant Q8_0|Q4_K_M] [--threads N]
#   scripts/embedder.sh stop
#   scripts/embedder.sh status
#
# Defaults are chosen for laptop friendliness: bounded threads (leaves cores
# for everything else), small micro-batch (low KV/RSS), nice'd priority.
set -euo pipefail

PORT="${OXIDE_EMBED_PORT:-8191}"
QUANT="Q8_0"
THREADS="${OXIDE_EMBED_THREADS:-8}"
PID_FILE="/tmp/opencode/oxide-embedder.pid"
LOG_FILE="/tmp/opencode/oxide-embedder.log"

ARGS=("$@")
if [[ "${1:-}" == "start" || "${1:-}" == "stop" || "${1:-}" == "status" ]]; then CMD="$1"; shift; fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quant) QUANT="$2"; shift 2 ;;
    --threads) THREADS="$2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 1 ;;
  esac
done

is_up() { curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; }

case "${CMD:-status}" in
  start)
    if is_up; then
      echo "already running on :${PORT} ($(cat "$PID_FILE" 2>/dev/null || echo '?'))"
      exit 0
    fi
    mkdir -p /tmp/opencode
    setsid nohup nice -n 10 ~/.local/bin/llama serve \
      -hf "Qwen/Qwen3-Embedding-0.6B-GGUF:${QUANT}" \
      --embedding --pooling last \
      --threads "$THREADS" --parallel 1 \
      -ub 2048 \
      --port "$PORT" > "$LOG_FILE" 2>&1 < /dev/null &
    echo $! > "$PID_FILE"
    for _ in $(seq 1 120); do
      sleep 2
      if is_up; then
        echo "up on :${PORT} quant=${QUANT} threads=${THREADS} pid=$(cat "$PID_FILE")"
        echo "export OXIDE_EMBED_URL=http://127.0.0.1:${PORT}/v1/embeddings OXIDE_EMBED_MODEL=qwen3-${QUANT}"
        exit 0
      fi
    done
    echo "failed to start; log:" >&2; tail -20 "$LOG_FILE" >&2; exit 1
    ;;
  stop)
    if [[ -f "$PID_FILE" ]]; then
      PID=$(cat "$PID_FILE")
      kill "$PID" 2>/dev/null || true
      # kill the whole process group (setsid leader)
      kill -- -"$PID" 2>/dev/null || true
      rm -f "$PID_FILE"
      echo "stopped $PID"
    else
      echo "not tracked; nothing to stop"
    fi
    ;;
  status)
    if is_up; then echo "running on :${PORT}"; else echo "down"; exit 1; fi
    ;;
esac
