# Changelog

## 0.9.0 — 2026-09-30

The first release prepared for a school district.

### Runs on the boards
- Runs on Pardus ETAP 23.4 (Debian 12): the app now uses nothing newer than
  GTK 4.8, libadwaita 1.2 and Python 3.11, and a Debian 12 container checks
  that on every change.
- A USB-stick bundle (`packaging/board/`) installs into a teacher's account
  without sudo, carries GTK 4, libadwaita and PyMuPDF for boards that lack
  them, writes a report back to the stick, and uninstalls cleanly.
- ETAP's long-press right click is held off while the window is active, so
  holding a page edge turns pages instead of opening a menu.
- A failure to start is shown in a window, with the log's path.
- Interactive activities open in the board's Chrome where WebKitGTK is absent.

### A new look
- An identity of its own: icon, a generated icon set, the Inter typeface, four
  themes (koyu, açık, sepya, gece) with one palette each.
- Library: a "Derse devam et" shelf with the last book at its page, cover-first
  cards, and search, grade filters and edit mode in a bar at the bottom.
- Reader: one slim header and a floating dock at the bottom of the screen with
  paging, a page number pad and scrubber, view mode, zoom, activities, search
  and a menu for theme, rotate, fullscreen and help. A progress line under the
  page. The book's cover shows until its first page is rendered.
- Activities are marked with amber corners and a numbered badge, and pulse
  once after a page turn.
- Focus mode: controls in a dock, a title, the activity's position on the page,
  and an instant preview from the page already on screen.
- Help is a touch cheat sheet first, keyboard shortcuts second.

### Fixed
- Scroll mode fitted its zoom to nothing, ignored the page it was asked to
  open, and rendered every bound row: two hundred page textures at once.
- Thumbnails were rendered for every bound row while the sidebar was closed,
  which delayed the first page of a book by seconds.
- Focus mode's first crop was fitted to a guessed stage size.
- Thumbnails no longer render ahead of the first visible page.
- The render worker no longer prints a traceback when a book is closed
  mid-render.

### Removed
- The web edition's leftovers in comments, the developer telemetry in the
  status bar, the detector-confidence badge on every card, English strings.
