#!/usr/bin/env bash
# Exercises nested constructs, dynamic references and script invocations.
LIB_DIR="./lib"
source "$LIB_DIR/util.sh"
source "${LIB_DIR}/missing.sh"

TASKS=(clean build test)
declare -A SEEN=()

run_all() {
  local task
  for task in "${TASKS[@]}"; do
    SEEN["$task"]=1
    case "$task" in
      clean) while read -r line; do printf '%s\n' "$line"; done < <(printf 'x\n') ;;
      build) ( cd ./lib && ./compile.sh ) ;;
      *) bash ./helper.sh "$task" ;;
    esac
  done
  total=${#TASKS[@]}
  echo "$(helper_name)"
}

helper_name() { printf 'helper\n'; }
run_all
