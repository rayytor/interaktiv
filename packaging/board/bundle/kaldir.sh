#!/bin/sh
# Interaktiv'i bu kullanıcıdan kaldırır: program, indirilen kitaplar, ayarlar.
#   sh kaldir.sh
data="${XDG_DATA_HOME:-$HOME/.local/share}"
config="${XDG_CONFIG_HOME:-$HOME/.config}"
cache="${XDG_CACHE_HOME:-$HOME/.cache}"

rm -f "$data/applications/org.interaktiv.School.desktop"
rm -f "$data/icons/hicolor/scalable/apps/org.interaktiv.School.svg"
rm -rf "${config:?}/interaktiv" "${cache:?}/interaktiv" "${data:?}/interaktiv"
echo "Interaktiv kaldırıldı."
