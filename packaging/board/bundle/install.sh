#!/bin/sh
# Installs Interaktiv from this folder into the user's own home folder.
# No sudo. Run again to update; books already installed are kept.
#   sh install.sh
set -e
src=$(cd "$(dirname "$0")" && pwd)
data="${XDG_DATA_HOME:-$HOME/.local/share}"
dest="$data/interaktiv"
applications="$data/applications"
icons="$data/icons/hicolor/scalable/apps"

mkdir -p "$dest" "$applications" "$icons"

# Books are large and may have been downloaded on this board: keep them.
kept=
if [ -d "$dest/app/books" ]; then
    kept=$(mktemp -d "$dest/.books.XXXXXX")
    find "$dest/app/books" -maxdepth 1 -name '*.pdf' -exec mv {} "$kept"/ \;
fi
for part in app lib typelibs python share lib-active typelibs-active; do
    rm -rf "${dest:?}/$part"
done

cp -r "$src/app" "$src/lib" "$src/typelibs" "$src/python" "$src/share" "$dest/"
cp "$src/interaktiv" "$src/activate.sh" "$src/kaldir.sh" "$src/icon.svg" "$dest/"
[ -f "$src/build-report.txt" ] && cp "$src/build-report.txt" "$dest/"
chmod +x "$dest/interaktiv"

if [ -n "$kept" ]; then
    mkdir -p "$dest/app/books"
    # A book that is also on the stick is the newer copy; do not replace it.
    find "$kept" -maxdepth 1 -name '*.pdf' -exec mv -n {} "$dest/app/books"/ \;
    rm -rf "${kept:?}"
fi

sh "$dest/activate.sh"

cp "$dest/icon.svg" "$icons/org.interaktiv.School.svg"
cat >"$applications/org.interaktiv.School.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Interaktiv
GenericName=Textbook Reader
GenericName[tr]=Ders Kitabı Okuyucu
Comment=Interactive textbooks for the smart board
Comment[tr]=Akıllı tahta için etkileşimli ders kitapları
Exec=$dest/interaktiv
Icon=org.interaktiv.School
Terminal=false
Categories=Education;
Keywords=kitap;ders;pdf;tahta;etkinlik;
StartupWMClass=org.interaktiv.School
DESKTOP

echo "Interaktiv kuruldu: $dest"
