#!/usr/bin/env bash
# Sourced into bin/entrypoint.sh; defines the shared functions.
source ./config.sh

log() {
  local message="$1"
  printf '%s\n' "$message"
  echo "$message" >> "$LOG_FILE"
}

log_last() {
  tail -n 1 "$LOG_FILE"
}

run_step() {
  local step="$1"
  case "$step" in
    clean) cleanup ;;
    build) compile ;;
    test) verify ;;
    *) fail "unknown step: $step" ;;
  esac
}

cleanup() { rm -rf ./build; }
compile() { make -s; }
verify() { ( cd ./tests && ../tests/check.sh ); }

fail() {
  log "error: $1"
  exit 1
}

finish() {
  local jobs
  jobs=$(jobs -p)
  printf 'done %s\n' "$jobs" | tee "$LOG_FILE"
}
