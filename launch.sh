#!/usr/bin/env bash
#
# Interaktiv School Edition Launcher
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: Python 3 not found on PATH." >&2
    exit 1
fi

exec python3 -m interaktiv_gtk "$@"
