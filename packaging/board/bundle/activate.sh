#!/bin/sh
# Chooses which bundled libraries this system needs. A library the system
# already has is left to the system; only the missing ones are linked into
# lib-active, which is what the launcher puts on the library path.
here=$(cd "$(dirname "$0")" && pwd)
known=$(/sbin/ldconfig -p 2>/dev/null || ldconfig -p 2>/dev/null)
tab=$(printf '\t')

rm -rf "${here:?}/lib-active" "${here:?}/typelibs-active"
mkdir -p "$here/lib-active" "$here/typelibs-active"

for library in "$here"/lib/*; do
    name=$(basename "$library")
    case "$known" in
        *"$tab$name ("*) ;;
        *) ln -s "../lib/$name" "$here/lib-active/$name" ;;
    esac
done

for typelib in "$here"/typelibs/*.typelib; do
    name=$(basename "$typelib")
    found=
    for directory in /usr/lib/x86_64-linux-gnu/girepository-1.0 /usr/lib/girepository-1.0 \
                     /usr/lib64/girepository-1.0; do
        [ -f "$directory/$name" ] && found=yes
    done
    [ -n "$found" ] || ln -s "../typelibs/$name" "$here/typelibs-active/$name"
done
