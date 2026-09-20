"""
One page of the book, drawn, with its activities on top of it.

This is a `Gtk.Widget` with a `do_snapshot`, not a `Gtk.DrawingArea`. A
DrawingArea means Cairo, and Cairo means a CPU `set_source_surface` and paint of
a five-megabyte surface on every frame -- on the software renderer a cheap board
falls back to, that is the difference between a page turn and a stutter. A
snapshot hands GSK the texture and lets it do the scaling.

Three behaviours here are worth naming because they are what makes the page feel
immediate:

  * While a render is in flight the previous texture keeps being painted,
    scaled into the new bounds. It is soft for a few tens of milliseconds and
    then it is sharp. The alternative -- blanking to white and waiting -- reads
    as a stall even when it is faster.
  * A stale texture is only reused when it is the same page at the same
    rotation. Painting page 41's pixels inside page 42's frame would be worse
    than painting nothing.
  * The hotspots are drawn into the same snapshot rather than being child
    widgets. A page carries dozens of pieces; one widget each means real layout
    work on every turn, and a widget tree can drift out of step with
    `linking.hit_test` -- which would put the rectangle a teacher sees and the
    rectangle a teacher can press in two different places.

The chips and pins are drawn at a fixed pixel size rather than scaled with the
page, matching the web, where the activity layer sits above the canvas and its
labels stay legible at any zoom.
"""

from typing import Optional, Tuple

from gi.repository import Gdk, GObject, Graphene, Gsk, Gtk, Pango

from interaktiv_core.geometry import PageTransform

from ..theme import get_theme_color_matrix
from .overlay import Pin, Spot


def _rgba(spec: str) -> Gdk.RGBA:
    colour = Gdk.RGBA()
    colour.parse(spec)
    return colour


# The paper the page is printed on, painted under the texture so that a page
# which has not arrived yet is a sheet rather than a hole.
PAPER = _rgba("#ffffff")
SHADOW = _rgba("rgba(0,0,0,0.35)")

# Ported from `css/viewer.css`. The quiet pair is the web's `?debug=activities`
# rendering, which is the only state in which it draws every region at once; the
# loud pair is `.activity-hotspot.hover`.
QUIET_FILL = _rgba("rgba(59,130,246,0.09)")
QUIET_BORDER = _rgba("rgba(59,130,246,0.55)")
ACTIVE_FILL = _rgba("rgba(59,130,246,0.25)")
ACTIVE_BORDER = _rgba("#3b82f6")
ACTIVE_GLOW = _rgba("rgba(59,130,246,0.25)")

CHIP_BG = _rgba("#3b82f6")
CHIP_FG = _rgba("#ffffff")
COUNT_BG = _rgba("rgba(24,26,31,0.88)")
COUNT_FG = _rgba("#e6e8ec")
PIN_BG = _rgba("#e08807")
PIN_FG = _rgba("#ffffff")

HOTSPOT_RADIUS = 6
CHIP_HEIGHT = 20
CHIP_PAD = 6
PIN_SIZE = 26

CHIP_FONT = "Bold 9"
COUNT_FONT = "8"
PIN_FONT = "Bold 11"

SEARCH_MATCH_FILL = _rgba("rgba(255, 235, 59, 0.40)")
SEARCH_MATCH_BORDER = _rgba("rgba(245, 124, 0, 0.50)")
SEARCH_ACTIVE_FILL = _rgba("rgba(255, 112, 67, 0.55)")
SEARCH_ACTIVE_BORDER = _rgba("#ff5722")
SEARCH_RADIUS = 3


class PageView(Gtk.Widget):
    __gtype_name__ = "InteraktivPageView"

    _current_theme: str = "dark"

    __gsignals__ = {
        # A hotspot or a pin was pressed. The payload is the `Spot` or `Pin`
        # itself, so the handler never has to look anything up again.
        "activity-activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
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

        click = Gtk.GestureClick()
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
        self.queue_draw()

    def clear_texture(self) -> None:
        self._texture = None
        self._crop_transform = None
        self.queue_draw()

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
        self.queue_draw()

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
        if usable:
            mat, vec = get_theme_color_matrix(self.theme)
            if mat is not None and vec is not None:
                snapshot.push_color_matrix(mat, vec)
                snapshot.append_scaled_texture(
                    self._texture, Gsk.ScalingFilter.TRILINEAR, bounds
                )
                snapshot.pop()
            else:
                snapshot.append_scaled_texture(
                    self._texture, Gsk.ScalingFilter.TRILINEAR, bounds
                )
        self.snapshot_search_highlights(snapshot)
        self.snapshot_overlay(snapshot, bounds)

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

    def _on_released(self, gesture, n_press, x, y) -> None:
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

        active = []
        for spot in overlay.spots:
            rect = transform.rect_to_widget(spot.rect)
            is_active = spot is self._hover or spot.act_index == self._selected
            if is_active:
                active.append((spot, rect))
            elif self._reveal:
                self._draw_spot(snapshot, rect, QUIET_FILL, QUIET_BORDER, 1.0)

        # The active piece is drawn last and with its chip, so a hotspot that
        # overlaps a neighbour is never covered by the thing it is on top of.
        for spot, rect in active:
            self._draw_spot(snapshot, rect, ACTIVE_FILL, ACTIVE_BORDER, 2.0, glow=True)
            self._draw_chip(snapshot, rect, spot)

        for pin in overlay.pins:
            self._draw_pin(snapshot, transform, pin)

    def _draw_spot(self, snapshot, rect, fill, border, width, glow=False) -> None:
        x, y, w, h = rect
        if w <= 0 or h <= 0:
            return
        graphene_rect = Graphene.Rect().init(x, y, w, h)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(graphene_rect, HOTSPOT_RADIUS)
        if glow:
            snapshot.append_outset_shadow(rounded, ACTIVE_GLOW, 0, 0, 3, 3)
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(fill, graphene_rect)
        snapshot.pop()
        snapshot.append_border(rounded, [width] * 4, [border] * 4)

    def _draw_chip(self, snapshot, rect, spot: Spot) -> None:
        x, y, _w, h = rect
        text = f"⚡ {spot.label}".strip() if spot.interactive else spot.label
        if text:
            self._draw_badge(
                snapshot, x - 8, y - CHIP_HEIGHT + 9, text, CHIP_FONT, CHIP_BG, CHIP_FG
            )
        if spot.item_count:
            label = f"{spot.item_count} soru"
            width = self._badge_width(label, COUNT_FONT)
            self._draw_badge(
                snapshot, x + rect[2] - width + 4, y + h - 9,
                label, COUNT_FONT, COUNT_BG, COUNT_FG,
            )

    def _layout(self, text: str, font: str) -> Pango.Layout:
        layout = self.create_pango_layout(text)
        layout.set_font_description(Pango.FontDescription(font))
        return layout

    def _badge_width(self, text: str, font: str) -> float:
        return self._layout(text, font).get_pixel_size()[0] + 2 * CHIP_PAD

    def _draw_badge(self, snapshot, x, y, text, font, background, foreground) -> None:
        layout = self._layout(text, font)
        text_w, text_h = layout.get_pixel_size()
        width = text_w + 2 * CHIP_PAD
        height = max(CHIP_HEIGHT, text_h + 4)
        rect = Graphene.Rect().init(x, y, width, height)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(rect, height / 2)
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(background, rect)
        snapshot.pop()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(x + CHIP_PAD, y + (height - text_h) / 2))
        snapshot.append_layout(layout, foreground)
        snapshot.restore()

    def _draw_pin(self, snapshot, transform, pin: Pin) -> None:
        cx, cy = self._pin_centre(transform, pin)
        rect = Graphene.Rect().init(
            cx - PIN_SIZE / 2, cy - PIN_SIZE / 2, PIN_SIZE, PIN_SIZE
        )
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(rect, PIN_SIZE / 2)
        snapshot.append_outset_shadow(rounded, SHADOW, 0, 3, 5, 0)
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(PIN_BG, rect)
        snapshot.pop()
        if pin is self._hover_pin:
            snapshot.append_border(rounded, [2] * 4, [CHIP_FG] * 4)
        layout = self._layout("⚡", PIN_FONT)
        text_w, text_h = layout.get_pixel_size()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(cx - text_w / 2, cy - text_h / 2))
        snapshot.append_layout(layout, PIN_FG)
        snapshot.restore()
        if pin is self._hover_pin:
            self._draw_badge(
                snapshot, cx + PIN_SIZE / 2 + 4, cy - CHIP_HEIGHT / 2,
                f"⚡ {pin.label}", CHIP_FONT, CHIP_BG, CHIP_FG,
            )
