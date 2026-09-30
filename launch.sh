#!/bin/sh
# Starts Interaktiv from this source checkout. On a board, use the bundle from
# packaging/board/ instead.
cd "$(dirname "$0")" || exit 1
exec python3 -m interaktiv_gtk "$@"
