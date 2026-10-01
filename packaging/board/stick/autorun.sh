#!/bin/sh
# Lives at the top of the USB stick. When the stick is plugged into a board the
# desktop asks whether to run the software on it; pressing "Çalıştır" runs this
# with /bin/sh. It installs Rayyan Ekitap into the user's home folder (no sudo),
# writes a short report back to the stick, and starts the app.
# By hand, from the stick's folder: sh autorun.sh
here=$(dirname "$0")
[ -d "$here/interaktiv" ] || here=$PWD
stick=
for candidate in "$here" /media/*/INTERAKTIV /run/media/*/INTERAKTIV /media/INTERAKTIV; do
    [ -f "$candidate/interaktiv/install.sh" ] && stick=$(cd "$candidate" && pwd) && break
done

say() {
    zenity "$1" --no-wrap --title="Rayyan Ekitap" --text="$2" 2>/dev/null \
        || notify-send "Rayyan Ekitap" "$2" 2>/dev/null || echo "$2"
}

if [ -z "$stick" ]; then
    say --error "Rayyan Ekitap dosyaları bulunamadı. Belleği çıkarıp yeniden takın."
    exit 1
fi

installed="${XDG_DATA_HOME:-$HOME/.local/share}/interaktiv"
results="$stick/results"
stamp=$(date +%Y%m%d-%H%M%S)
report="$results/rapor-$stamp.txt"
install_log="$results/kurulum-$stamp.txt"
mkdir -p "$results" 2>/dev/null

# Copying the books can take a minute: say so instead of looking frozen. The
# progress window closes when the installer, on the other end of the pipe, ends.
status_file=$(mktemp)
run_install() {
    sh "$stick/interaktiv/install.sh" >"$install_log" 2>&1
    echo $? >"$status_file"
}
if command -v zenity >/dev/null 2>&1; then
    run_install | zenity --progress --pulsate --auto-close --no-cancel \
        --title="Rayyan Ekitap" --text="Rayyan Ekitap kuruluyor…" 2>/dev/null
fi
[ -s "$status_file" ] || run_install
status=$(cat "$status_file")
rm -f "$status_file"

# What this board is, for whoever reads the stick afterwards.
{
    echo "== tarih"; date
    echo "== sistem"; cat /etc/os-release 2>/dev/null
    echo "== masaüstü"; echo "${XDG_CURRENT_DESKTOP:-?} ${XDG_SESSION_TYPE:-?}"
    echo "== ekran"; xrandr --query 2>/dev/null | head -n 12
    echo "== python"; python3 --version 2>&1
    echo "== selftest"; "$installed/interaktiv" --selftest 2>&1
    echo "== kurulum (kod $status)"; cat "$install_log" 2>/dev/null
} >"$report" 2>&1
sync

if [ "$status" != 0 ] || ! grep -q '^selftest: ok' "$report"; then
    say --error "Rayyan Ekitap bu tahtada çalıştırılamadı.

Rapor belleğe yazıldı: results/rapor-$stamp.txt
Bu pencerenin fotoğrafını çekip geliştiriciye gönderin."
    exit 1
fi

cd "$HOME" || exit 1   # not on the stick, or it cannot be ejected while the app runs
exec "$installed/interaktiv"
