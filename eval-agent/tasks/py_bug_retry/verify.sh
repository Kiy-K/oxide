#!/usr/bin/env bash
cd "$(dirname "$0")" && python3 -m unittest discover -s tests -v > /dev/null 2>&1
