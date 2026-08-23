#!/usr/bin/env bash
cd "$(dirname "$0")" && bun test tests/ > /dev/null 2>&1
