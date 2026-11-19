#!/bin/sh
# Shebang says POSIX sh but the body uses Bash-only syntax: partial coverage.
VALUES=(one two)

check() {
  if [[ -f ./input ]]; then
    (( COUNT++ ))
  fi
  function extra() { echo extra; }
  extra
}

check
