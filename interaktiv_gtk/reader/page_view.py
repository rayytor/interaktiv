"""
One page of the book, drawn, with its activities on top of it.

This is a `Gtk.Widget` with a `do_snapshot`, not a `Gtk.DrawingArea`: a
snapshot hands GSK the page's texture and lets it do the scaling, where a
drawing area would paint a multi-megabyte surface through Cairo every frame.

Three things keep the page feeling immediate:

  * While a render is in flight the previous texture keeps being painted,
    scaled into the new bounds: soft for a moment, then sharp. Blanking to
    white and waiting reads as a stall even when it is faster.
  * A stale texture is only reused for the same page at the same rotation.
  * The hotspots are drawn into the same snapshot instead of being child
    widgets, and are hit-tested by `linking.hit_test`, so the rectangle a
    teacher sees and the rectangle a teacher can press cannot drift apart.

An activity is marked quietly: amber corners and a small badge, so the page
stays a page. The marks are a fixed size whatever the zoom, and amber because
the books themselves print their activity boxes in blue.
"""

from typing import List, Optional, Sequence, Tuple

from gi.repository import Gdk, GLib, GObject, Graphene, Gsk, Gtk, Pango

from interaktiv_core.geometry import PageTransform

from ..theme import get_theme_color_matrix
from ..touch import TAP_SLOP, is_touch
from .overlay import Pin, Spot


def _rgba(spec: str) -> Gdk.RGBA:
    colour = Gdk.RGBA()
    colour.parse(spec)
    return colour


def _faded(colour: Gdk.RGBA, alpha: float) -> Gdk.RGBA:
    faded = colour.copy()
    faded.alpha = alpha
    return faded


# The paper the page is printed on, painted under the texture so that a page
# which has not arrived yet is a sheet rather than a hole.
PAPER = _rgba("#ffffff")
SHADOW = _rgba("rgba(0,0,0,0.35)")

# An activity at rest, and one that is selected or under the pointer.
MARK = _rgba("#f08c00")
MARK_FILL = _rgba("#ffb020")
QUIET_FILL_ALPHA = 0.07
ACTIVE_FILL_ALPHA = 0.20
ACTIVE_GLOW = _rgba("rgba(240,140,0,0.35)")

BADGE_BG = _rgba("#ffb020")
BADGE_FG = _rgba("#2b1a00")
COUNT_BG = _rgba("rgba(24,26,31,0.88)")
COUNT_FG = _rgba("#e6e8ec")
PIN_BG = _rgba("#ffb020")
PIN_FG = _rgba("#2b1a00")

HOTSPOT_RADIUS = 8
CORNER_LENGTH = 16.0
CORNER_WIDTH = 2.5
BADGE_HEIGHT = 24
BADGE_PAD = 8
PIN_SIZE = 40

BADGE_FONT = "Inter Bold 11"
COUNT_FONT = "Inter Bold 10"
PIN_FONT = "Inter Bold 15"

# The mark an interactive activity carries: it opens something that plays.
PLAY = "▶"

# A page with more activities than this is marked with corners alone: a badge
# on each of thirty questions is noise, not a map.
CROWDED = 8

# How long the marks stay lit after a page is turned to, so that a class sees
# where the activities are before they settle back.
PULSE_MS = 900.0
PULSE_FILL_ALPHA = 0.26

SEARCH_MATCH_FILL = _rgba("rgba(255, 235, 59, 0.40)")
SEARCH_MATCH_BORDER = _rgba("rgba(245, 124, 0, 0.50)")
SEARCH_ACTIVE_FILL = _rgba("rgba(255, 112, 67, 0.55)")
SEARCH_ACTIVE_BORDER = _rgba("#ff5722")
SEARCH_RADIUS = 3


def short_label(spot: Spot) -> str:
    """What fits in a badge: the activity's letter or number, else its position."""
    label = (spot.label or "").strip()
    return label if 0 < len(label) <= 3 else str(spot.act_index + 1)


class PageView(Gtk.Widget):
    __gtype_name__ = "InteraktivPageView"

    _current_theme: str = "dark"

    __gsignals__ = {
        # A hotspot or a pin was pressed. The payload is the `Spot` or `Pin`
        # itself, so the handler never has to look anything up again.
        "activity-activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # Bare paper was double-tapped. There is no payload: what a second tap
        # means is the reader's business, not the page's.
        "zoom-toggled": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__()
        self.page = 0
        self._texture = None
        self._texture_page = 0
        self._texture_rotation = 0
        self._width = 1.0
        self._height = 1.0
        self._pdf_size = (0.0, 0.0)
        self.rotation = 0
        self._crop_transform: Optional[PageTransform] = None
        # A stand-in until the real render arrives: a texture of the whole
        # page and the part of it (left, top, width, height as fractions) to show.
        self._preview: Optional[Tuple[object, Tuple[float, float, float, float]]] = None
        self._pulse_started: Optional[int] = None
        self._pulse_tick = 0
        self._theme: Optional[str] = None
        self.set_overflow(Gtk.Overflow.VISIBLE)

        self._overlay = None
        self._overlay_page = 0
        self._reveal = True
        self._hover: Optional[Spot] = None
        self._hover_pin: Optional[Pin] = None
        self._selected: Optional[int] = None
        self._search_matches: List[Tuple[float, float, float, float]] = []
        self._active_search_index: Optional[int] = None

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

        self._press: Optional[Tuple[float, float]] = None
        self._press_touch = False

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_pressed)
        click.connect("released", self._on_released)
        self.add_controller(click)

    # -- what to draw -----------------------------------------------------

    def set_page(self, page: int, rotation: int) -> None:
        """
        Point the view at a page. Dropping the texture when the page changes is
        deliberate: the recycled widget must not flash the page it used to hold.
        """
        if page != self.page or rotation % 360 != self.rotation:
            if page != self._texture_page or rotation % 360 != self._texture_rotation:
                self._texture = None
            self._hover = None
            self._hover_pin = None
            self._crop_transform = None
            self._search_matches = []
            self._active_search_index = None
        self.page = page
        self.rotation = rotation % 360

    def set_search_matches(
        self,
        matches: Sequence[Tuple[float, float, float, float]],
        active_index: Optional[int] = None,
    ) -> None:
        self._search_matches = list(matches)
        self._active_search_index = active_index
        self.queue_draw()

    def clear_search_matches(self) -> None:
        self._search_matches = []
        self._active_search_index = None
        self.queue_draw()

    def set_pdf_size(self, width: float, height: float) -> None:
        """The page's own size in PDF points, before rotation or scaling."""
        self._pdf_size = (float(width), float(height))

    def set_layout_size(self, width: float, height: float) -> None:
        """The size the page occupies on screen, in logical pixels."""
        width = max(1.0, float(width))
        height = max(1.0, float(height))
        if (width, height) == (self._width, self._height):
            return
        self._width, self._height = width, height
        self.queue_resize()

    def set_crop_transform(self, transform: Optional[PageTransform]) -> None:
        """Set an explicit PageTransform for crop/focus mode."""
        self._crop_transform = transform

    def set_texture(self, texture, page: int, rotation: int) -> None:
        self._texture = texture
        self._texture_page = page
        self._texture_rotation = rotation % 360
        self._preview = None
        self.queue_draw()

    def clear_texture(self) -> None:
        self._texture = None
        self._crop_transform = None
        self._preview = None
        self.queue_draw()

    def set_preview(self, texture, part: Tuple[float, float, float, float]) -> None:
        """
        Show `part` of a whole-page texture until this view's own render
        arrives. `part` is (left, top, width, height) as fractions of the page.
        """
        self._preview = (texture, part)
        self.queue_draw()

    @property
    def texture(self):
        """The texture on screen, if it is this page's."""
        if self._texture is not None and self._texture_page == self.page:
            return self._texture
        return None

    @property
    def has_texture(self) -> bool:
        return self._texture is not None

    @property
    def layout_size(self) -> Tuple[float, float]:
        return (self._width, self._height)

    # -- activities -------------------------------------------------------

    def set_overlay(self, overlay, page: int) -> None:
        if overlay is self._overlay and page == self._overlay_page:
            return
        self._overlay = overlay
        self._overlay_page = page
        self._hover = None
        self._hover_pin = None
        if overlay is not None and overlay.spots:
            self._start_pulse()
        self.queue_draw()

    def _start_pulse(self) -> None:
        self._pulse_started = None
        if not self._pulse_tick:
            self._pulse_tick = self.add_tick_callback(self._on_pulse_tick)

    def _on_pulse_tick(self, _widget, clock) -> bool:
        now = clock.get_frame_time()
        if self._pulse_started is None:
            self._pulse_started = now
        self.queue_draw()
        if (now - self._pulse_started) / 1000.0 >= PULSE_MS:
            self._pulse_tick = 0
            self._pulse_started = None
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _pulse(self) -> float:
        """1.0 when a page has just appeared, easing to 0.0."""
        if self._pulse_started is None or not self._pulse_tick:
            return 0.0
        clock = self.get_frame_clock()
        if clock is None:
            return 0.0
        elapsed = (clock.get_frame_time() - self._pulse_started) / 1000.0
        remaining = max(0.0, 1.0 - elapsed / PULSE_MS)
        return remaining * remaining

    def set_reveal(self, reveal: bool) -> None:
        reveal = bool(reveal)
        if reveal == self._reveal:
            return
        self._reveal = reveal
        self.queue_draw()

    def set_selected(self, act_index: Optional[int]) -> None:
        """
        Which activity is picked out, by position on the sheet.

        A whole activity, not one of its pieces: choosing it from the sidebar
        should show both halves of a region that flows across a column break.
        The pointer is the thing that lights one piece at a time.
        """
        if act_index == self._selected:
            return
        self._selected = act_index
        self.queue_draw()

    @property
    def overlay(self):
        return self._overlay if self._overlay_page == self.page else None

    @classmethod
    def set_global_theme(cls, theme: str) -> None:
        cls._current_theme = theme

    def set_theme(self, theme: Optional[str]) -> None:
        if self._theme == theme:
            return
        self._theme = theme
        self.queue_draw()

    @property
    def theme(self) -> str:
        return self._theme or PageView._current_theme

    # -- GTK --------------------------------------------------------------

    def do_measure(self, orientation, for_size):
        # The minimum is one pixel on purpose. The spread lays its pages out
        # explicitly and a minimum of the natural size would make a window
        # smaller than one page impossible to shrink.
        natural = self._width if orientation == Gtk.Orientation.HORIZONTAL else self._height
        return (1, int(round(natural)), -1, -1)

    def do_snapshot(self, snapshot) -> None:
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0:
            return
        bounds = Graphene.Rect().init(0, 0, width, height)

        self._snapshot_shadow(snapshot, bounds)
        snapshot.append_color(PAPER, bounds)

        usable = (
            self._texture is not None
            and self._texture_page == self.page
            and self._texture_rotation == self.rotation
        )
        if usable or self._preview is not None:
            mat, vec = get_theme_color_matrix(self.theme)
            tinted = mat is not None and vec is not None
            if tinted:
                snapshot.push_color_matrix(mat, vec)
            if usable:
                self._append_texture(snapshot, bounds)
            else:
                self._append_preview(snapshot, bounds)
            if tinted:
                snapshot.pop()
        self.snapshot_search_highlights(snapshot)
        self.snapshot_overlay(snapshot, bounds)

    def _append_texture(self, snapshot, bounds) -> None:
        # Trilinear filtering keeps a page sharp while it is drawn smaller than
        # it was rendered, mid-pinch. GTK 4.8 on the oldest boards only has the
        # linear `append_texture`.
        if hasattr(snapshot, "append_scaled_texture"):
            snapshot.append_scaled_texture(  # floor: ok
                self._texture, Gsk.ScalingFilter.TRILINEAR, bounds
            )
        else:
            snapshot.append_texture(self._texture, bounds)

    def _append_preview(self, snapshot, bounds) -> None:
        texture, (left, top, part_w, part_h) = self._preview
        if part_w <= 0 or part_h <= 0:
            return
        # The whole page, placed so that the wanted part lands on the bounds.
        width, height = bounds.get_width(), bounds.get_height()
        full_w, full_h = width / part_w, height / part_h
        whole = Graphene.Rect().init(-left * full_w, -top * full_h, full_w, full_h)
        snapshot.push_clip(bounds)
        snapshot.append_texture(texture, whole)
        snapshot.pop()

    def snapshot_search_highlights(self, snapshot) -> None:
        if not self._search_matches:
            return
        transform = self.transform()
        if transform is None:
            return

        for idx, rect in enumerate(self._search_matches):
            wx, wy, ww, wh = transform.rect_to_widget(rect)
            if ww <= 0 or wh <= 0:
                continue
            graphene_rect = Graphene.Rect().init(wx, wy, ww, wh)
            rounded = Gsk.RoundedRect()
            rounded.init_from_rect(graphene_rect, SEARCH_RADIUS)

            is_active = (idx == self._active_search_index)
            if is_active:
                snapshot.append_outset_shadow(
                    rounded, _rgba("rgba(255, 87, 34, 0.40)"), 0, 0, 3, 2
                )
                snapshot.push_rounded_clip(rounded)
                snapshot.append_color(SEARCH_ACTIVE_FILL, graphene_rect)
                snapshot.pop()
                snapshot.append_border(rounded, [2] * 4, [SEARCH_ACTIVE_BORDER] * 4)
            else:
                snapshot.push_rounded_clip(rounded)
                snapshot.append_color(SEARCH_MATCH_FILL, graphene_rect)
                snapshot.pop()
                snapshot.append_border(rounded, [1] * 4, [SEARCH_MATCH_BORDER] * 4)

    def _snapshot_shadow(self, snapshot, bounds) -> None:
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(bounds, 0)
        snapshot.append_outset_shadow(rounded, SHADOW, 0, 2, 4, 0)

    # -- geometry ---------------------------------------------------------

    def transform(self) -> Optional[PageTransform]:
        """
        The mapping this widget is currently drawing under.

        The scale is read back off the allocation rather than being handed down
        from the spread, so the rectangles cannot end up on a different scale
        from the pixels they sit on -- whatever rounding the layout did, the
        hotspots did the same rounding.
        """
        if self._crop_transform is not None:
            return self._crop_transform
        page_w, page_h = self._pdf_size
        if page_w <= 0 or page_h <= 0:
            return None
        rot = self.rotation % 360
        span = page_h if rot % 180 == 90 else page_w
        width = self.get_width()
        if width <= 0 or span <= 0:
            return None
        return PageTransform.for_full_page(page_w, page_h, width / span, rot)

    def _pin_centre(self, transform: PageTransform, pin: Pin) -> Tuple[float, float]:
        """
        Where the publisher hung an icon, in widget pixels.

        `posx`/`posy` are percentages of the sheet measured from its top-left
        corner, which is the one corner PDF user space does not have -- hence
        the flip through `page_h` before the transform sees it.
        """
        page_w, page_h = self._pdf_size
        x = page_w * (pin.x_pct / 100.0)
        y = page_h - page_h * (pin.y_pct / 100.0)
        return transform.point_to_widget(x, y)

    # -- pointer ----------------------------------------------------------

    def _probe(self, wx: float, wy: float):
        """What sits under a widget point: a pin, a spot, or nothing."""
        overlay = self.overlay
        transform = self.transform()
        if overlay is None or transform is None:
            return (None, None)
        # Pins are tested first and in widget space: they are a fixed 26 px
        # marker whatever the zoom, so their reach is a pixel radius and not a
        # rectangle on the sheet.
        for pin in overlay.pins:
            cx, cy = self._pin_centre(transform, pin)
            if abs(wx - cx) <= PIN_SIZE / 2 and abs(wy - cy) <= PIN_SIZE / 2:
                return (None, pin)
        px, py = transform.point_to_pdf(wx, wy)
        return (overlay.hit_test(px, py), None)

    def _on_motion(self, _controller, x, y) -> None:
        spot, pin = self._probe(x, y)
        if spot is self._hover and pin is self._hover_pin:
            return
        self._hover, self._hover_pin = spot, pin
        interactive = (spot is not None and spot.interactive) or pin is not None
        if pin is not None or spot is not None:
            self.set_cursor(Gdk.Cursor.new_from_name(
                "pointer" if interactive else "zoom-in", None
            ))
        else:
            self.set_cursor(None)
        self.queue_draw()

    def _on_leave(self, _controller) -> None:
        if self._hover is None and self._hover_pin is None:
            return
        self._hover = None
        self._hover_pin = None
        self.set_cursor(None)
        self.queue_draw()

    def _on_pressed(self, gesture, _n_press, x, y) -> None:
        self._press = (x, y)
        self._press_touch = is_touch(gesture)

    def _on_released(self, gesture, n_press, x, y) -> None:
        """
        A tap opens what is under it; a second tap on bare paper zooms.

        The slop check is the touch half of this. A finger that panned the
        sheet and happened to lift over a hotspot has not asked for that
        activity, and the scroller does not always cancel us first -- it only
        claims a drag once it passes GTK's threshold, and a two-finger pinch
        leaves this gesture holding the first finger on its own.
        """
        press, self._press = self._press, None
        if press is not None and self._press_touch:
            if max(abs(x - press[0]), abs(y - press[1])) > TAP_SLOP:
                return

        if n_press == 2:
            # Only over bare paper: the first tap of a double tap on a hotspot
            # has already opened it, and zooming underneath that would be a
            # second answer to one gesture.
            spot, pin = self._probe(x, y)
            if spot is None and pin is None:
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self.emit("zoom-toggled")
            return
        if n_press != 1:
            return

        spot, pin = self._probe(x, y)
        target = pin if pin is not None else spot
        if target is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.emit("activity-activated", target)

    # -- overlay ----------------------------------------------------------

    def snapshot_overlay(self, snapshot, bounds) -> None:
        overlay = self.overlay
        if overlay is None:
            return
        transform = self.transform()
        if transform is None:
            return

        pulse = self._pulse()
        crowded = len(overlay.activities) > CROWDED
        active = []
        for spot in overlay.spots:
            rect = transform.rect_to_widget(spot.rect)
            if spot is self._hover or spot.act_index == self._selected:
                active.append((spot, rect))
            elif self._reveal:
                self._draw_quiet(snapshot, rect, spot, pulse, badge=not crowded)

        # The active piece is drawn last, so a hotspot that overlaps a
        # neighbour is never covered by the thing it is on top of.
        for spot, rect in active:
            self._draw_active(snapshot, rect, spot)

        for pin in overlay.pins:
            self._draw_pin(snapshot, transform, pin)

    def _draw_quiet(self, snapshot, rect, spot: Spot, pulse: float,
                    badge: bool = True) -> None:
        x, y, w, h = rect
        if w <= 0 or h <= 0:
            return
        bounds = Graphene.Rect().init(x, y, w, h)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(bounds, HOTSPOT_RADIUS)
        alpha = QUIET_FILL_ALPHA + (PULSE_FILL_ALPHA - QUIET_FILL_ALPHA) * pulse
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(_faded(MARK_FILL, alpha), bounds)
        snapshot.pop()
        self._draw_corners(snapshot, x, y, w, h)
        if badge and spot.part_index == 0:
            self._draw_badge(snapshot, x - 7, y - BADGE_HEIGHT / 2, self._badge_text(spot),
                             BADGE_FONT, BADGE_BG, BADGE_FG)

    def _draw_corners(self, snapshot, x, y, w, h) -> None:
        """Four corner marks: enough to say "this box", without drawing a box."""
        length = min(CORNER_LENGTH, w / 3.0, h / 3.0)
        t = CORNER_WIDTH
        for cx, cy, dx, dy in ((x, y, 1, 1), (x + w, y, -1, 1),
                               (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
            horizontal = Graphene.Rect().init(
                cx if dx > 0 else cx - length, cy if dy > 0 else cy - t, length, t)
            vertical = Graphene.Rect().init(
                cx if dx > 0 else cx - t, cy if dy > 0 else cy - length, t, length)
            snapshot.append_color(MARK, horizontal)
            snapshot.append_color(MARK, vertical)

    def _draw_active(self, snapshot, rect, spot: Spot) -> None:
        x, y, w, h = rect
        if w <= 0 or h <= 0:
            return
        bounds = Graphene.Rect().init(x, y, w, h)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(bounds, HOTSPOT_RADIUS)
        snapshot.append_outset_shadow(rounded, ACTIVE_GLOW, 0, 0, 4, 3)
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(_faded(MARK_FILL, ACTIVE_FILL_ALPHA), bounds)
        snapshot.pop()
        snapshot.append_border(rounded, [2.5] * 4, [MARK] * 4)

        text = spot.label.strip() if spot.label else short_label(spot)
        if spot.interactive:
            text = f"{PLAY} {text}"
        self._draw_badge(snapshot, x - 7, y - BADGE_HEIGHT / 2, text,
                         BADGE_FONT, BADGE_BG, BADGE_FG)
        if spot.item_count:
            label = f"{spot.item_count} soru"
            width = self._badge_width(label, COUNT_FONT)
            self._draw_badge(snapshot, x + w - width + 5, y + h - BADGE_HEIGHT / 2,
                             label, COUNT_FONT, COUNT_BG, COUNT_FG)

    @staticmethod
    def _badge_text(spot: Spot) -> str:
        text = short_label(spot)
        return f"{PLAY} {text}" if spot.interactive else text

    def _layout(self, text: str, font: str) -> Pango.Layout:
        layout = self.create_pango_layout(text)
        layout.set_font_description(Pango.FontDescription(font))
        return layout

    def _badge_width(self, text: str, font: str) -> float:
        return max(BADGE_HEIGHT, self._layout(text, font).get_pixel_size()[0] + 2 * BADGE_PAD)

    def _draw_badge(self, snapshot, x, y, text, font, background, foreground) -> None:
        layout = self._layout(text, font)
        text_w, text_h = layout.get_pixel_size()
        height = max(BADGE_HEIGHT, text_h + 4)
        width = max(height, text_w + 2 * BADGE_PAD)
        rect = Graphene.Rect().init(x, y, width, height)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(rect, height / 2)
        snapshot.append_outset_shadow(rounded, SHADOW, 0, 1, 4, 0)
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(background, rect)
        snapshot.pop()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(
            x + (width - text_w) / 2, y + (height - text_h) / 2
        ))
        snapshot.append_layout(layout, foreground)
        snapshot.restore()

    def _draw_pin(self, snapshot, transform, pin: Pin) -> None:
        cx, cy = self._pin_centre(transform, pin)
        rect = Graphene.Rect().init(
            cx - PIN_SIZE / 2, cy - PIN_SIZE / 2, PIN_SIZE, PIN_SIZE
        )
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(rect, PIN_SIZE / 2)
        snapshot.append_outset_shadow(rounded, SHADOW, 0, 3, 6, 0)
        if pin is self._hover_pin:
            snapshot.append_outset_shadow(rounded, ACTIVE_GLOW, 0, 0, 6, 2)
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(PIN_BG, rect)
        snapshot.pop()
        layout = self._layout(PLAY, PIN_FONT)
        text_w, text_h = layout.get_pixel_size()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(cx - text_w / 2 + 1, cy - text_h / 2))
        snapshot.append_layout(layout, PIN_FG)
        snapshot.restore()
        if pin is self._hover_pin:
            self._draw_badge(
                snapshot, cx + PIN_SIZE / 2 + 6, cy - BADGE_HEIGHT / 2,
                pin.label, BADGE_FONT, BADGE_BG, BADGE_FG,
            )
