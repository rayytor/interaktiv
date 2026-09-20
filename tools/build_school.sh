#!/usr/bin/env bash
#
# Build the standalone, lightweight Interaktiv School Edition.
# Assembles dist/interaktiv-school/ containing only the files necessary
# for school/smartboard use: reader webapp + packaged library + JIT activity loader.
# Excludes authoring/converter tools, test suites, and unbaked books.
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${ROOT_DIR}/dist/interaktiv-school"

echo "Building Interaktiv School Edition..."
echo "Destination: ${DIST_DIR}"

rm -rf "${DIST_DIR}"
mkdir -p "${DIST_DIR}"

# 1. Core Python runtime
cp "${ROOT_DIR}/main.py" "${DIST_DIR}/"
cp "${ROOT_DIR}/server.py" "${DIST_DIR}/"
cp "${ROOT_DIR}/books_manager.py" "${DIST_DIR}/"

# 2. Frontend assets
cp "${ROOT_DIR}/index.html" "${DIST_DIR}/"
cp -r "${ROOT_DIR}/css" "${DIST_DIR}/"
cp -r "${ROOT_DIR}/js" "${DIST_DIR}/"

# 3. Branding & icons
for f in icon.png icon.svg favicon.ico; do
    if [ -f "${ROOT_DIR}/${f}" ]; then
        cp "${ROOT_DIR}/${f}" "${DIST_DIR}/"
    fi
done

# 4. Catalog, thumbnails & metadata (no PDFs bundled by default)
echo "Copying catalog, thumbnails and metadata..."
cp "${ROOT_DIR}/kitap_pdf_linkleri.txt" "${DIST_DIR}/"
cp -r "${ROOT_DIR}/thumbnails" "${DIST_DIR}/"
cp -r "${ROOT_DIR}/activities_meta" "${DIST_DIR}/"

mkdir -p "${DIST_DIR}/activities"
if [ -d "${ROOT_DIR}/activities/books" ]; then
    cp -r "${ROOT_DIR}/activities/books" "${DIST_DIR}/activities/"
fi

# 5. Local books storage (empty, user-installed PDFs go here)
mkdir -p "${DIST_DIR}/books"

# 6. Optional pre-packaged library bundles (if present)
if [ -d "${ROOT_DIR}/library" ]; then
    echo "Copying library bundles..."
    cp -r "${ROOT_DIR}/library" "${DIST_DIR}/"
fi

# 7. School launcher script
# Modifies launch.sh to run with --edition school
sed 's|\("${PYTHON_EXEC}" main.py --no-browser --port "${PORT}" --auto-shutdown\) "$@"|\1 --edition school "$@"|g' \
    "${ROOT_DIR}/launch.sh" > "${DIST_DIR}/launch.sh"
chmod +x "${DIST_DIR}/launch.sh"

# 8. School README
cat << 'EOF' > "${DIST_DIR}/README.md"
# Interaktiv - Okul / Akıllı Tahta Sürümü (School Edition)

Bu sürüm, akıllı tahtalarda ve okul bilgisayarlarında hızlı, çevrimdışı ve dokunmatik çalışacak şekilde optimize edilmiştir.

## Başlatma

Terminalde:
```bash
./launch.sh
```

Veya doğrudan Python ile:
```bash
python3 main.py --edition school
```

## Özellikler
- **Tüm Kitap Kataloğu ve Kapaklar**: Tüm ders kitaplarının linkleri, kapak görselleri ve bilgileri hazır gelir.
- **Önizleme ve İndirme**: Kitaplar doğrudan internet üzerinden önizlenebilir; istenen kitaplar tek tıkla çevrimdışı kullanım için cihaza indirilebilir.
- **Akıllı Tahta Dostu**: Geniş dokunma alanları, tam ekran ve odak modu desteği.
- **JIT Etkileşimli Etkinlikler**: Etkinlikler tıklandığında anında (JIT) yüklenir ve yerel olarak önbelleğe alınır.
EOF

echo "Build complete: ${DIST_DIR}"
