#!/bin/sh
# POSIX-compatible configuration, sourced by both entrypoint and util.
LOG_FILE="./build/run.log"
MAX_RETRIES=3

load_defaults() {
  DEFAULT_TARGET="all"
  DEFAULT_MODE="release"
}
