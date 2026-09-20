# Interaktiv - School Edition (GTK4 / Libadwaita)

A blazing-fast, lightweight, native PDF reader application engineered specifically for classroom smartboards and school environments to handle complex textbooks with instant render times and a strictly bounded memory footprint.

Built with Python, PyGObject (GTK4 & Libadwaita), and PyMuPDF.

## Highlights & Features

- 🖥️ **100% Native Desktop App**: Runs directly on GTK4 and Libadwaita without a browser, local web server, or Node runtime.
- 📖 **Two-Page Book Spread (Default)**: Authentic open-book viewing experience with facing pages (even on left, odd on right), center spine shading, and standalone cover display.
- 🔄 **3 Switchable View Modes**:
  - **Two-Page Book Spread** (`B`): Dual facing pages.
  - **Single Page View** (`S`): Clean single page centered display.
  - **Continuous Vertical Scroll** (`C`): Virtualized list with instant texture management.
- 🎯 **Pre-Baked Activity Hotspots + Click-to-Zoom**: Every activity and sub-question is precomputed with sub-millimeter precision. Clicking an activity enters focus zoom mode with pure crop rendering (saving memory).
- ⚡ **Interactive HTML Activities**: Publisher interactive HTML activities open directly in a native Libadwaita dialog powered by WebKitGTK.
- 🌓 **4 Reading Themes**:
  - **Dark** (Default, deep slate palette for eye comfort)
  - **Light** (Crisp clean paper tone)
  - **Sepia** (Warm retro book tone)
  - **Inverted** (Night reading contrast mode)
  All themes apply via instant GPU colour matrix filters on texture snapshots without CPU rasterization passes or cache invalidation.
- 🔍 **Full-Text Search (`Ctrl+F`)**: Instant in-document search with match counts, keyword highlighting, and navigation.
- 📑 **Thumbnails, Bookmarks & Activities Sidebar**: Drawer with lazy-rendered page thumbnail cards, document outline bookmarks, and activity list.
- 🖵 **Fullscreen Presentation Mode (`F`)**: Distraction-free full-screen reading for smartboard projection.
- ⌨️ **Smartboard & Keyboard Optimized**: Large touch targets (≥ 48px), hold-repeat paging buttons, and keyboard navigation.

---

## Quick Start

### 1. Launch Reader

Run the launcher script:

```bash
./launch.sh
```

Or run the Python module directly:

```bash
python3 -m interaktiv_gtk
```

### 2. Desktop Launcher & Installation

To install the application desktop entry and icon for the current user:

```bash
cp org.interaktiv.School.desktop ~/.local/share/applications/
cp icon.svg ~/.local/share/icons/hicolor/scalable/apps/org.interaktiv.School.svg
```

---

## Keyboard Shortcuts

| Keybinding | Action |
|---|---|
| <kbd>→</kbd> / <kbd>PageDown</kbd> / <kbd>Space</kbd> / <kbd>J</kbd> | Next page / spread |
| <kbd>←</kbd> / <kbd>PageUp</kbd> / <kbd>K</kbd> | Previous page / spread |
| <kbd>Home</kbd> / <kbd>End</kbd> | Jump to First / Last page |
| <kbd>B</kbd> | Two-Page Book Spread Mode |
| <kbd>S</kbd> | Single Page Mode |
| <kbd>C</kbd> | Continuous Vertical Scroll Mode |
| <kbd>+</kbd> / <kbd>-</kbd> / <kbd>0</kbd> | Zoom In / Zoom Out / Reset Zoom (Fit Page) |
| <kbd>Ctrl</kbd> + <kbd>Wheel</kbd> | Smooth Zoom with Mouse Wheel |
| <kbd>Ctrl</kbd> + <kbd>F</kbd> | Open Search Bar |
| <kbd>T</kbd> | Toggle Thumbnails / Bookmarks Sidebar |
| <kbd>M</kbd> | Cycle Theme (Dark → Light → Sepia → Inverted) |
| <kbd>R</kbd> | Rotate Page Clockwise (90°) |
| <kbd>F</kbd> | Toggle Fullscreen Presentation |
| <kbd>?</kbd> | View Keyboard Shortcuts Cheat Sheet |
| <kbd>Click</kbd> on an activity | Zoom into that activity (focus mode) |
| <kbd>A</kbd> | Toggle Activity Hotspots on / off |
| <kbd>→</kbd> / <kbd>←</kbd> *(in focus)* | Next / Previous activity (crosses pages) |
| <kbd>↓</kbd> / <kbd>↑</kbd> *(in focus)* | Next / Previous numbered question |
| <kbd>+</kbd> / <kbd>-</kbd> / <kbd>0</kbd> *(in focus)* | Nudge / reset the focus zoom |
| <kbd>Esc</kbd> | Exit focus / Close Modals / Close Search |

---

## Architecture

```
interaktiv/
├── launch.sh              # Standalone School Edition launcher script
├── books_manager.py       # Textbook catalogue, download & activity cache manager
├── org.interaktiv.School.desktop # Desktop entry file
├── icon.svg, icon.png     # Application icons
├── interaktiv_core/       # Core headless modules (no GTK dependency)
│   ├── geometry.py        # PDF coordinate math & transformations
│   ├── jobs.py            # JIT bakes, activity resolution & downloads
│   ├── linking.py         # Hotspot to interactive activity matching
│   ├── oges.py            # Publisher interactive index
│   ├── regions.py         # BAKE_VERSION 2 regions.json loader
│   └── appdirs.py         # XDG config and cache directory resolution
├── interaktiv_gtk/        # Native GTK4 / Libadwaita front end
│   ├── __main__.py        # CLI entry point (python3 -m interaktiv_gtk)
│   ├── app.py             # Adw.Application
│   ├── window.py          # Adw.ApplicationWindow + navigation
│   ├── theme.py           # Color matrix math and Libadwaita theme controller
│   ├── style.css          # School touch metrics & theme overrides
│   ├── library/           # Catalogue view, cards, grade filter tabs, downloads
│   ├── reader/            # Reader view, spread, scroll mode, focus zoom, sidebar
│   ├── render/            # Dedicated render worker thread, texture cache, requests
│   └── state/             # JSON settings persistence
├── tools/
│   ├── build_school.sh    # Distribution packaging script
│   ├── build_school_gtk.sh # GTK distribution packaging script
│   └── hotspot_extraction/ # PyMuPDF-based scanner and packaging tools
└── tests/                 # Comprehensive pytest test suite
```

---

## Packaging

To package a standalone distribution bundle of Interaktiv School Edition:

```bash
bash tools/build_school.sh
```

The output bundle is assembled in `dist/interaktiv-school/`.
