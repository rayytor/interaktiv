# The floor: Debian 12, the base of Pardus ETAP 23.4, with its own GTK 4.8 and
# libadwaita 1.2. The board bundle is assembled from this image's libraries and
# the test suite is run against them.
#
#   docker build -t interaktiv-floor packaging/board
FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
        python3 python3-pip python3-gi python3-pytest \
        gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-graphene-1.0 \
        libglib2.0-bin binutils xvfb xauth adwaita-icon-theme librsvg2-common \
        fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# The Python packages a board does not have, as abi3 wheels: one copy serves
# Python 3.11 on ETAP 23.4 and 3.13 on ETAP 25.
RUN pip3 install --quiet --no-cache-dir --only-binary=:all: \
        --target /opt/interaktiv/python pymupdf psutil \
 && find /opt/interaktiv/python -type d -name __pycache__ -prune -exec rm -rf {} + \
 && rm -rf /opt/interaktiv/python/bin
