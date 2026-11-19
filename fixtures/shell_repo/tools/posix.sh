#!/bin/sh
# Pure POSIX sh: no Bash-only constructs, so coverage stays complete.
GREETING="hello"

say_hello() {
  echo "$GREETING $1"
}

if [ -n "$1" ]; then
  say_hello "$1"
else
  say_hello world
fi
