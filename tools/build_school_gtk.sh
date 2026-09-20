#!/usr/bin/env bash
#
# Build the standalone, lightweight Interaktiv School Edition (GTK4 / libadwaita).
# Assembles dist/interaktiv-school-gtk/ containing only the files necessary
# for school/smartboard use: native GTK reader + core runtime + packaged library.
#
# Strictly excludes webapp frontend assets (index.html, css/, js/) and server
# components (server.py, main.py).
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${ROOT_DIR}/dist/interaktiv-school-gtk"

echo "Building Interaktiv School Edition (GTK4)..."
echo "Destination: ${DIST_DIR}"

rm -rf "${DIST_DIR}"
mkdir -p "${DIST_DIR}"

# 1. Core Python runtime & GTK package
echo "Copying Python runtime and GTK application..."
cp "${ROOT_DIR}/books_manager.py" "${DIST_DIR}/"
cp -r "${ROOT_DIR}/interaktiv_core" "${DIST_DIR}/"
cp -r "${ROOT_DIR}/interaktiv_gtk" "${DIST_DIR}/"

# Clean any pycache in dist
find "${DIST_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# 2. Desktop entry & branding
echo "Installing desktop launcher and icons..."
if [ -f "${ROOT_DIR}/org.interaktiv.School.desktop" ]; then
    cp "${ROOT_DIR}/org.interaktiv.School.desktop" "${DIST_DIR}/"
fi
for f in icon.png icon.svg; do
    if [ -f "${ROOT_DIR}/${f}" ]; then
        cp "${ROOT_DIR}/${f}" "${DIST_DIR}/"
    fi
done

# Standard XDG desktop structure for easy system integration
mkdir -p "${DIST_DIR}/share/applications"
mkdir -p "${DIST_DIR}/share/icons/hicolor/scalable/apps"
mkdir -p "${DIST_DIR}/share/icons/hicolor/256x256/apps"

if [ -f "${ROOT_DIR}/org.interaktiv.School.desktop" ]; then
    cp "${ROOT_DIR}/org.interaktiv.School.desktop" "${DIST_DIR}/share/applications/"
fi
if [ -f "${ROOT_DIR}/icon.svg" ]; then
    cp "${ROOT_DIR}/icon.svg" "${DIST_DIR}/share/icons/hicolor/scalable/apps/org.interaktiv.School.svg"
fi
if [ -f "${ROOT_DIR}/icon.png" ]; then
    cp "${ROOT_DIR}/icon.png" "${DIST_DIR}/share/icons/hicolor/256x256/apps/org.interaktiv.School.png"
fi

# 3. Catalog, thumbnails & metadata
echo "Copying catalog, thumbnails and metadata..."
cp "${ROOT_DIR}/kitap_pdf_linkleri.txt" "${DIST_DIR}/"
cp -r "${ROOT_DIR}/thumbnails" "${DIST_DIR}/"
cp -r "${ROOT_DIR}/activities_meta" "${DIST_DIR}/"

mkdir -p "${DIST_DIR}/activities"
if [ -d "${ROOT_DIR}/activities/books" ]; then
    cp -r "${ROOT_DIR}/activities/books" "${DIST_DIR}/activities/"
fi

# 4. Local books storage (empty, user-installed PDFs go here)
mkdir -p "${DIST_DIR}/books"

# 5. Optional pre-packaged library bundles (if present)
if [ -d "${ROOT_DIR}/library" ]; then
    echo "Copying library bundles..."
    cp -r "${ROOT_DIR}/library" "${DIST_DIR}/"
fi

# 6. Standalone GTK launcher script
cat << 'EOF' > "${DIST_DIR}/launch.sh"
#!/usr/bin/env bash
#
# Interaktiv GTK School Edition Launcher
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: Python 3 not found on PATH." >&2
    exit 1
fi

exec python3 -m interaktiv_gtk --edition school "$@"
EOF
chmod +x "${DIST_DIR}/launch.sh"

# 7. School GTK README
cat << 'EOF' > "${DIST_DIR}/README.md"
# Interaktiv - Okul / Akıllı Tahta Sürümü (GTK4 / libadwaita)

Bu sürüm, akıllı tahtalarda ve okul bilgisayarlarında ultra-hızlı, yerel (native),
çevrimdışı ve dokunmatik çalışacak şekilde GTK4 ve libadwaita ile geliştirilmiştir.
Web tarayıcısı veya yerel HTTP sunucusu gerektirmez.

## Başlatma

Terminalde:
```bash
./launch.sh
```

Veya doğrudan Python modülü ile:
```bash
python3 -m interaktiv_gtk --edition school
```

Masaüstü uygulaması olarak kurmak için:
```bash
cp org.interaktiv.School.desktop ~/.local/share/applications/
cp icon.svg ~/.local/share/icons/hicolor/scalable/apps/org.interaktiv.School.svg
```

## Özellikler
- **Yerel GTK4 / Libadwaita Arayüzü**: Tarayıcı yükü olmadan anında açılış ve düşük bellek tüketimi.
- **Dört Farklı Tema**: Koyu (dark), açık (light), sepia ve karşıt (inverted) renk filtreleri.
- **Akıllı Tahta Optimizasyonu**: Geniş dokunmatik hedefler (>= 48px), kolay sayfa çevirme butonları ve klavye kısayolları.
- **Etkileşimli Etkinlikler ve Odak Modu**: PDF üzerindeki etkinlik alanlarını vurgulama ve tek tıkla soruya odaklanma.
- **Çevrimdışı Kullanım**: İndirilen kitaplar ve önbelleğe alınan etkinlikler internet bağlantısı olmadan çalışır.
EOF

# 8. Sanity check: Ensure web frontend and server files are NOT bundled
for excluded in "index.html" "css" "js" "server.py" "main.py"; do
    if [ -e "${DIST_DIR}/${excluded}" ]; then
        echo "Error: Excluded item '${excluded}' was found in ${DIST_DIR}" >&2
        exit 1
    fi
done

echo "Build complete: ${DIST_DIR}"
