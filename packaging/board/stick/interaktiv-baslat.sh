#!/bin/sh
# Starts Rayyan Ekitap from a USB stick it shares with other programs: the RAYYANPEN
# stick's app window runs this from its "Interaktiv" button (demos.ini). It
# installs Rayyan Ekitap into the user's home folder (no sudo) only when the stick
# holds a different build or different books from the ones installed, so a
# lesson starts in seconds instead of copying the books again; it writes a short
# report to the stick's results folder and then starts the app.
# By hand, from the stick's folder: sh interaktiv-baslat.sh
here=$(dirname "$0")
[ -d "$here/interaktiv" ] || here=$PWD
stick=
for candidate in "$here" /media/*/RAYYANPEN /run/media/*/RAYYANPEN \
                 /media/*/INTERAKTIV /run/media/*/INTERAKTIV; do
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
mkdir -p "$results" 2>/dev/null

# What is on the stick: the build and the books. Installed when it differs.
wanted=$( { cat "$stick/interaktiv/build-report.txt" 2>/dev/null
            ls -l "$stick/interaktiv/app/books" 2>/dev/null | awk '{print $5, $NF}'; } )
have=$(cat "$installed/stick-stamp.txt" 2>/dev/null)

status=0
if [ "$wanted" != "$have" ] || [ ! -x "$installed/interaktiv" ]; then
    install_log="$results/kurulum-$stamp.txt"
    status_file=$(mktemp)
    run_install() {
        sh "$stick/interaktiv/install.sh" >"$install_log" 2>&1
        echo $? >"$status_file"
    }
    # Copying the books takes a minute: say so instead of looking frozen.
    if command -v zenity >/dev/null 2>&1; then
        run_install | zenity --progress --pulsate --auto-close --no-cancel \
            --title="Rayyan Ekitap" --text="Rayyan Ekitap kuruluyor…" 2>/dev/null
    fi
    [ -s "$status_file" ] || run_install
    status=$(cat "$status_file")
    rm -f "$status_file"

    report="$results/rapor-$stamp.txt"
    {
        echo "== tarih"; date
        echo "== sistem"; cat /etc/os-release 2>/dev/null
        echo "== masaüstü"; echo "${XDG_CURRENT_DESKTOP:-?} ${XDG_SESSION_TYPE:-?}"
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
    printf '%s\n' "$wanted" >"$installed/stick-stamp.txt"
fi

cd "$HOME" || exit 1   # not on the stick, or it cannot be ejected while the app runs
exec "$installed/interaktiv"
