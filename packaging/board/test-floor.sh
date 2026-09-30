#!/bin/sh
# Runs the test suite, and optionally the screenshot tool, on the oldest
# toolkit the app supports: Debian 12's GTK 4.8, libadwaita 1.2, Python 3.11.
#
#   packaging/board/test-floor.sh                 # tests
#   packaging/board/test-floor.sh --shots DIR     # tests, then screenshots into DIR
#
# The working tree is mounted read-only; nothing in it is changed.
set -eu

root=$(cd "$(dirname "$0")/../.." && pwd)
engine=${ENGINE:-}
shots=

while [ $# -gt 0 ]; do
    case "$1" in
        --shots) shots=$2; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$engine" ]; then
    for candidate in docker podman; do
        command -v "$candidate" >/dev/null 2>&1 && engine=$candidate && break
    done
fi
[ -n "$engine" ] || { echo "docker or podman is needed." >&2; exit 1; }

"$engine" build -q -t interaktiv-floor "$root/packaging/board" >/dev/null

mounts="-v $root:/src:ro"
command='cd /src && python3 -m compileall -q -d /src interaktiv_gtk interaktiv_core >/dev/null
xvfb-run -a python3 -m pytest tests -q -p no:cacheprovider'
if [ -n "$shots" ]; then
    mkdir -p "$shots"
    mounts="$mounts -v $(cd "$shots" && pwd):/shots"
    command="$command && python3 tools/screenshots.py --out /shots"
fi

# shellcheck disable=SC2086  # $mounts is a list of options
"$engine" run --rm $mounts \
    -e PYTHONPATH=/opt/interaktiv/python -e PYTHONDONTWRITEBYTECODE=1 \
    -e PYTHONPYCACHEPREFIX=/tmp/pycache \
    -e GDK_BACKEND=x11 -e GSK_RENDERER=cairo -e GTK_A11Y=none \
    -e HOME=/tmp/home --user "$(id -u):$(id -g)" \
    interaktiv-floor sh -c "mkdir -p /tmp/home && $command"
