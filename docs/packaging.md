# Packaging for Pardus ETAP boards

Boards run Pardus ETAP 23.4 (Debian 12) today and ETAP 25 (Debian 13) as they
are reinstalled. Teachers have no sudo, so nothing can be installed from a
`.deb`; the app installs into the teacher's own home folder from a USB stick.
The facts about the boards themselves are in the ETAP application guide kept
with this project's notes.

## What the bundle contains

`packaging/board/build.sh` assembles `dist/interaktiv-board/`:

```
autorun.sh              Run by the desktop when the stick is plugged in ("Çalıştır").
OKU-BENI.txt            One page for the teacher.
interaktiv/
  interaktiv            The launcher.
  install.sh            Copies the folder to ~/.local/share/interaktiv, writes the menu entry.
  activate.sh           Picks which bundled libraries this system needs.
  kaldir.sh             Uninstalls.
  app/                  The application and its data (catalogue, covers, bakes, books).
  lib/                  GTK 4, libadwaita and what they link, from Debian 12.
  typelibs/             Their GObject introspection typelibs.
  python/               PyMuPDF and psutil, as abi3 wheels (Python 3.11 and later).
  share/                GTK 4's compiled settings schemas.
  build-report.txt      Versions, and the newest glibc symbol in the bundle.
```

The libraries come from a `debian:bookworm` container (`Dockerfile`), so the
newest glibc symbol any of them needs is 2.36, which the oldest board has. The
system's own glibc, GLib, graphics driver, X11, fonts and D-Bus are never
bundled: `activate.sh` links into `lib-active/` only the libraries the board
lacks (`ldconfig -p` decides), so on ETAP 25, which has GTK 4.18, the system's
GTK is used and only the typelibs come from the bundle.

WebKitGTK is not bundled. Where it is missing, the publisher's interactive
activities open in the board's Chrome as an app window.

## Building

```bash
packaging/board/build.sh --books 3d372038,09f62a7e   # with these installed books
packaging/board/build.sh --all-installed-books
```

Needs Docker or Podman. The first build creates the `interaktiv-floor` image
(about 1 GB on disk); later builds take seconds. Copy the contents of
`dist/interaktiv-board/` to the top of a stick labelled `INTERAKTIV`
(FAT32 or exFAT; the label is how `autorun.sh` finds itself).

## Testing without a board

```bash
packaging/board/test-floor.sh --shots /tmp/floor      # tests + screenshots on GTK 4.8 / libadwaita 1.2
packaging/board/test-bundle.sh --shots /tmp/board     # install and start the bundle on a GTK 3-only Debian 12
python3 tools/check_api_floor.py                      # API newer than the floor, from the GIR files
```

`test-bundle.sh` uses a second image, `interaktiv-board-sim`: Debian 12 with
Python, GTK 3 and PyGObject only, which is what a board that has never seen
GTK 4 offers. It installs the bundle as an ordinary user, runs
`interaktiv --selftest` and takes the same screenshots.

## Before a board visit

- Built from a clean checkout; `build-report.txt` says GLIBC ≤ 2.36.
- `test-floor.sh` and `test-bundle.sh` pass.
- Tested in the ETAP 25 VM at 3840 × 2160 and 200 %: plug the stick, press
  Çalıştır, then by touch: edge tap, swipe, pinch, three fingers together, the
  search field brings up the ETA keyboard, the bottom panel stays visible.
- On the board: `results/rapor-<date>.txt` on the stick brings back the
  self-test and the system report. A failure to start is shown in a window
  with the log's path.
