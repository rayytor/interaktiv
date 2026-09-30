#!/bin/sh
# Builds the smart-board bundle: a folder that goes on a USB stick and installs
# Interaktiv into a teacher's home folder on Pardus ETAP, without sudo.
#
#   packaging/board/build.sh                        # the app, no books
#   packaging/board/build.sh --books 09f62a7e,cb558332
#   packaging/board/build.sh --all-installed-books  # every PDF in books/
#   packaging/board/build.sh --books ... --stick /media/$USER/RAYYANPEN
#       # and put it on a stick another program already uses (the RAYYANPEN
#       # stick): its interaktiv/ folder and interaktiv-baslat.sh are replaced,
#       # nothing else on the stick is touched
#
# The libraries are taken from debian:bookworm, the base of ETAP 23.4, so the
# bundle runs on the oldest board and on everything newer. Needs docker or
# podman. Output: dist/interaktiv-board/ (see docs/packaging.md).
set -eu

root=$(cd "$(dirname "$0")/../.." && pwd)
here="$root/packaging/board"
out="$root/dist/interaktiv-board"
books=
engine=
stick=

while [ $# -gt 0 ]; do
    case "$1" in
        --books) books=$2; shift 2 ;;
        --all-installed-books) books=all; shift ;;
        --engine) engine=$2; shift 2 ;;
        --stick) stick=$2; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$engine" ]; then
    for candidate in docker podman; do
        command -v "$candidate" >/dev/null 2>&1 && engine=$candidate && break
    done
fi
[ -n "$engine" ] || { echo "docker or podman is needed to build the bundle." >&2; exit 1; }

# On disk under dist/, not in /tmp: /tmp is often RAM-backed, and the bundle
# with its books is too large to build in memory.
mkdir -p "$root/dist"
work=$(mktemp -d "$root/dist/.board-build.XXXXXX")
trap 'rm -rf "${work:?}"' EXIT

# 1. The application and its data, exactly as the reader loads them.
app="$work/app"
mkdir -p "$app/activities" "$app/books"
cp "$root/kitap_pdf_linkleri.txt" "$app/"
cp -r "$root/interaktiv_core" "$root/interaktiv_gtk" "$root/thumbnails" \
      "$root/activities_meta" "$app/"
cp -r "$root/activities/books" "$app/activities/"
find "$app" -type d -name __pycache__ -prune -exec rm -rf {} +
# Detector diagnostics: the reader only loads regions.json.
find "$app/activities/books" \( -name 'trace.json.gz' -o -name 'diagnostics.json.gz' \) -delete

# 2. Libraries, typelibs and Python packages, from Debian 12. The image is
#    built once and reused; see Dockerfile.
"$engine" build -q -t interaktiv-floor "$here" >/dev/null
cp "$here/container-build.sh" "$work/"
"$engine" run --rm -e HOSTUID="$(id -u):$(id -g)" -v "$work:/work" \
    interaktiv-floor sh /work/container-build.sh

# 3. Put it together.
rm -rf "${out:?}"
mkdir -p "$out/interaktiv"
cp -r "$app" "$out/interaktiv/app"
cp -r "$work/out/lib" "$work/out/typelibs" "$work/out/python" "$work/out/share" \
      "$out/interaktiv/"
cp "$here/bundle/interaktiv" "$here/bundle/install.sh" "$here/bundle/kaldir.sh" \
   "$here/bundle/activate.sh" "$out/interaktiv/"
cp "$root/interaktiv_gtk/icons/hicolor/scalable/apps/org.interaktiv.School.svg" \
   "$out/interaktiv/icon.svg"
cp "$here/stick/autorun.sh" "$here/stick/OKU-BENI.txt" \
   "$here/stick/interaktiv-baslat.sh" "$out/"
cp "$work/out/build-report.txt" "$out/interaktiv/"

# 4. Books for a board with no network.
if [ "$books" = all ]; then
    cp "$root"/books/*.pdf "$out/interaktiv/app/books/"
elif [ -n "$books" ]; then
    for prefix in $(echo "$books" | tr ',' ' '); do
        found=$(find "$root/books" -maxdepth 1 -name "$prefix*.pdf" | head -n 1)
        [ -n "$found" ] || { echo "no installed book starts with $prefix" >&2; exit 1; }
        cp "$found" "$out/interaktiv/app/books/"
    done
fi

echo
echo "Bundle: $out ($(du -sh "$out" | cut -f1))"
echo "Copy its contents to the top of a USB stick labelled INTERAKTIV."

if [ -n "$stick" ]; then
    [ -d "$stick" ] || { echo "no such stick folder: $stick" >&2; exit 1; }
    rm -rf "${stick:?}/interaktiv"
    cp -r "$out/interaktiv" "$stick/"
    cp "$out/interaktiv-baslat.sh" "$stick/"
    sync
    echo "Copied to $stick: interaktiv/ and interaktiv-baslat.sh"
fi
