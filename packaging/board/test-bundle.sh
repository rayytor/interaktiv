#!/bin/sh
# Installs and starts the built bundle on a stand-in for a board that has no
# GTK 4 at all (Dockerfile.board), the way a teacher's account would:
# install.sh into the home folder, then the self-test and the screenshots.
#
#   packaging/board/test-bundle.sh [--shots DIR]
#
# Needs dist/interaktiv-board/ from build.sh.
set -eu

root=$(cd "$(dirname "$0")/../.." && pwd)
bundle="$root/dist/interaktiv-board"
engine=${ENGINE:-}
shots=

while [ $# -gt 0 ]; do
    case "$1" in
        --shots) shots=$2; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

[ -f "$bundle/interaktiv/install.sh" ] || { echo "run packaging/board/build.sh first." >&2; exit 1; }
if [ -z "$engine" ]; then
    for candidate in docker podman; do
        command -v "$candidate" >/dev/null 2>&1 && engine=$candidate && break
    done
fi
[ -n "$engine" ] || { echo "docker or podman is needed." >&2; exit 1; }

"$engine" build -q -t interaktiv-board-sim -f "$root/packaging/board/Dockerfile.board" \
    "$root/packaging/board" >/dev/null

mounts="-v $bundle:/media/ogretmen/INTERAKTIV:ro -v $root/tools/screenshots.py:/tools/screenshots.py:ro"
command='sh /media/ogretmen/INTERAKTIV/interaktiv/install.sh
app=$HOME/.local/share/interaktiv
ls "$app/lib-active" | wc -l | sed "s/^/bundled libraries in use: /"
GDK_BACKEND=x11 GSK_RENDERER=cairo GTK_A11Y=none xvfb-run -a "$app/interaktiv" --selftest'
if [ -n "$shots" ]; then
    mkdir -p "$shots"
    chmod a+rwx "$shots"
    mounts="$mounts -v $(cd "$shots" && pwd):/shots"
    # The screenshot tool is started with the launcher's own environment.
    command="$command
export LD_LIBRARY_PATH=\$app/lib-active GI_TYPELIB_PATH=\$app/typelibs-active
export GSETTINGS_SCHEMA_DIR=\$app/share/glib-2.0/schemas PYTHONPATH=\$app/python:\$app/app
mkdir -p /tmp/run/tools && cp /tools/screenshots.py /tmp/run/tools/ && ln -s \$app/app/* /tmp/run/
python3 /tmp/run/tools/screenshots.py --out /shots"
fi

# shellcheck disable=SC2086  # $mounts is a list of options
"$engine" run --rm $mounts interaktiv-board-sim sh -ec "$command"
