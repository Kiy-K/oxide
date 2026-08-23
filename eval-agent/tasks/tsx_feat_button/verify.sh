#!/usr/bin/env bash
cd "$(dirname "$0")"
bun install --silent > /dev/null 2>&1 || true
bun test tests/ > /dev/null 2>&1
