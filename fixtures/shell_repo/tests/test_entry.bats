#!/usr/bin/env bats

@test "entrypoint sources utilities" {
  source ../lib/util.sh
  run log "hello"
  [ "$status" -eq 0 ]
}
