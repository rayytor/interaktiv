# Changelog

## Unreleased

### Rayyan Ekitap
- Interaktiv is now called Rayyan Ekitap: the window, the menu entry, the
  messages and the documents use the new name. Folders, the menu entry's id
  and the stick's files keep their names, so an installed copy updates in
  place and keeps its books, settings and drawings.
- New icon: an open book beside a tablet.
- The about window names the author, with a link.

### Drawing on the book
- Strokes drawn with Rayyanpen over a page are kept by the book: they stay on
  the printed line through zoom, scroll, page turns, view modes and rotation,
  and are there the next time the book is opened. Rayyanpen's eraser, undo,
  redo and clear reach them too.
- A stroke across two facing pages is kept on both.
- Every button of Rayyan Ekitap works while Rayyanpen's pen is out: Rayyanpen
  takes taps only over the pages.
- Each book keeps up to 5 MB of drawings; past that, the oldest strokes are
  deleted first.
- The dock's menu can delete the drawings on the pages on screen, with undo.

### Touch like a phone
- Pinch zooms around the point between the fingers, and moving both fingers
  pans the page in the same gesture. The page is not re-rendered until the
  fingers lift or hold still.
- A double tap zooms around the tapped point; the double tap is tuned for
  fingers (40 px, 400 ms).
- Zooming goes to 500 %.
- While zoomed in, the invisible page-edge strips step aside, so a finger at
  the edge pans instead of turning the page. A tap on an activity under a strip
  opens the activity.
- A tap that misses an activity by up to 12 px still opens it.

### Boards
- The window's X class is `org.interaktiv.School`, so the menu entry and the
  panel recognise the running app.
- `interaktiv-baslat.sh` starts Rayyan Ekitap from the RAYYANPEN stick's window,
  installing only when the stick has something new.

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
