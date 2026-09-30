#!/bin/sh
# Runs inside the interaktiv-floor image, started by build.sh. Collects what a
# board does not have -- GTK 4, libadwaita, their typelibs, PyMuPDF -- into
# /work/out.
set -eu
multiarch=/usr/lib/x86_64-linux-gnu
out=/work/out
report=$out/build-report.txt

mkdir -p "$out/lib" "$out/typelibs" "$out/share/glib-2.0/schemas"
cp -r /opt/interaktiv/python "$out/python"

# GTK 4 and libadwaita with everything they link, except what must come from
# the running system: the C library, the graphics driver, X11, fonts, D-Bus
# and GLib (etap_app_guide.md section 3.2).
system='ld-linux|libc\.so|libm\.so|libpthread|libdl\.so|librt\.so|libGL|libEGL|libGLX|libGLdispatch|libdrm|libgbm|libX|libxcb|libfontconfig|libfreetype|libdbus|libsystemd|libglib-2|libgobject-2|libgio-2|libgmodule-2|libgthread-2|libgcc_s|libstdc\+\+'
for library in "$multiarch/libgtk-4.so.1" "$multiarch/libadwaita-1.so.0"; do
    cp -L "$library" "$out/lib/"
    ldd "$library" | awk '/=> \//{print $3}'
done | sort -u | grep -vE "$system" | while read -r dependency; do
    cp -L "$dependency" "$out/lib/"
done

for package in gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-graphene-1.0; do
    dpkg -L "$package" | grep '\.typelib$' | while read -r typelib; do
        cp -L "$typelib" "$out/typelibs/"
    done
done

# GTK 4 aborts if it needs one of its own settings schemas and cannot find it.
cp /usr/share/glib-2.0/schemas/org.gtk.gtk4.*.gschema.xml "$out/share/glib-2.0/schemas/"
glib-compile-schemas "$out/share/glib-2.0/schemas"

# What the bundle was built from, and the newest glibc any part of it asks for.
{
    echo "built: $(date -u +%Y-%m-%dT%H:%M:%SZ) in $(. /etc/os-release && echo "$PRETTY_NAME")"
    echo "gtk: $(dpkg-query -W -f='${Version}' libgtk-4-1)"
    echo "libadwaita: $(dpkg-query -W -f='${Version}' libadwaita-1-0)"
    echo "python: $(python3 --version | cut -d' ' -f2)"
    echo "pymupdf: $(PYTHONPATH=$out/python python3 -c 'import pymupdf; print(pymupdf.__version__)')"
    newest=$(find "$out" -name '*.so*' -type f -exec objdump -T {} + 2>/dev/null \
        | grep -o 'GLIBC_[0-9.]*' | sort -Vu | tail -n 1)
    echo "newest glibc symbol: $newest (the oldest board has GLIBC_2.36)"
} > "$report"
cat "$report"

chown -R "$HOSTUID" /work
