#!/usr/bin/env bash
# Cross-platform Python orchestrator; Linux / macOS / Git Bash convenience wrapper.
set -euo pipefail
cd "$(dirname "$0")/.."
python scripts/run.py --input "${1:-examples/majority.sop}" "${@:2}"
