#!/usr/bin/env bash
# Declares the same function as lib/util.sh but is never sourced.
log() { printf 'other\n'; }
