#!/usr/bin/env bash
cd "$(dirname "$0")" && python3 -m unittest discover -s tests > /dev/null 2>&1
