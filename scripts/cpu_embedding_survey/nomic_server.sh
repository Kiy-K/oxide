#!/usr/bin/env bash
# Survey-only llama.cpp embedding server for Nomic Embed v2 MoE. Mirrors
# scripts/embedder.sh's conventions (same PID/log pattern, same nice'd
# priority) but on a separate port so it can run alongside the production
# Qwen3 server without colliding.
#
# Usage:
#   scripts/cpu_embedding_survey/nomic_server.sh start [--quant Q8_0] [--threads N]
#   scripts/cpu_embedding_survey/nomic_server.sh stop
#   scripts/cpu_embedding_survey/nomic_server.sh status
set -euo pipefail

PORT="${OXIDE_NOMIC_EMBED_PORT:-8192}"
QUANT="Q8_0"
THREADS="${OXIDE_EMBED_THREADS:-8}"
PID_FILE="/tmp/opencode/oxide-nomic-embedder.pid"
LOG_FILE="/tmp/opencode/oxide-nomic-embedder.log"

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
    # No --pooling override: the official GGUF's metadata already encodes
    # Nomic's documented mean pooling (unlike Qwen3, which needs an explicit
    # --pooling last override) — verified empirically via
    # examples/nomic_correctness_check before any timing is trusted, not
    # assumed from this comment alone.
    setsid nohup nice -n 10 ~/.local/bin/llama serve \
      -hf "nomic-ai/nomic-embed-text-v2-moe-GGUF:${QUANT}" \
      --embedding \
      --threads "$THREADS" --parallel 1 \
      -ub 2048 \
      --port "$PORT" > "$LOG_FILE" 2>&1 < /dev/null &
    echo $! > "$PID_FILE"
    for _ in $(seq 1 120); do
      sleep 2
      if is_up; then
        echo "up on :${PORT} quant=${QUANT} threads=${THREADS} pid=$(cat "$PID_FILE")"
        echo "export OXIDE_EMBED_URL=http://127.0.0.1:${PORT}/v1/embeddings OXIDE_EMBED_MODEL=nomic-v2-moe-${QUANT}"
        exit 0
      fi
    done
    echo "failed to start; log:" >&2; tail -20 "$LOG_FILE" >&2; exit 1
    ;;
  stop)
    if [[ -f "$PID_FILE" ]]; then
      PID=$(cat "$PID_FILE")
      kill "$PID" 2>/dev/null || true
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
