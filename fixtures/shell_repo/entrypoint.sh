#!/usr/bin/env bash
# Top-level entry point: sources utilities, then uses their functions.
set -euo pipefail

source ./lib/util.sh
. ./lib/config.sh

readonly APP_NAME="widget"
export LOG_LEVEL="info"
PREFIX="[$APP_NAME]"

main() {
  local target="${1:-all}"
  log "$PREFIX building $target"
  for step in clean build test; do
    run_step "$step"
  done
  if [ "$target" = "all" ]; then
    finish
  fi
  report "$(log_last)"
}

main "$@"
