"""
Focus mode: crop-zooming an activity, part, or numbered question.

On a classroom smartboard, a teacher touches an activity to enlarge it for the
whole room. A region that flows across multiple columns is zoomed piece by piece
-- never as a unioned bounding box covering everything in between.

Focus mode owns the stage while active:
  * Navigation (<-/->, N/P, Space, PageUp/Down) steps across activities.
  * Sub-item navigation (^/v) steps through numbered questions.
  * Zoom bias nudging (+/-/0) scales the crop with instant feedback.
  * Tapping the background or pressing Esc restores the pre-focus state exactly.
"""

import math
from typing import Optional, Tuple

from gi.repository import Gdk, GLib, Gtk, Pango

from interaktiv_core.geometry import PageTransform
from interaktiv_core.regions import Activity

from .. import icons
from ..touch import TAP_SLOP, SwipeNavigator, bind_touch_tooltip, is_touch
from .overlay import activity_label
from .page_view import PageView

Rect = Tuple[float, float, float, float]

MAX_FOCUS_PIXELS = 8_000_000.0


def calculate_focus_scale(
    stage_w: float,
    stage_h: float,
    region_w: float,
    region_h: float,
    rotation: int = 0,
    zoom_bias: float = 1.0,
    scale_factor: float = 1.0,
) -> Tuple[float, float]:
    """
    Compute logical `scale` and device `render_scale` for a focus crop.

    Clamps:
      - bias in [0.5, 3.0]
      - s in [0.4, 6.0]
      - area in pixels <= 8,000,000 px (both logical and device)
    """
    rot = rotation % 360
    dev_w = region_h if rot % 180 == 90 else region_w
    dev_h = region_w if rot % 180 == 90 else region_h

    avail_w = max(240.0, stage_w - 56.0)
    avail_h = max(240.0, stage_h - 56.0)

    bias = max(0.5, min(3.0, float(zoom_bias)))

    if dev_w > 0 and dev_h > 0:
        s = min(avail_w / dev_w, avail_h / dev_h) * bias
    else:
        s = 1.0
    s = max(0.4, min(6.0, s))

    area = region_w * region_h
    if area > 0:
        s = min(s, math.sqrt(MAX_FOCUS_PIXELS / area))

    render_scale = s * max(1.0, float(scale_factor))
    if area > 0:
        render_scale = min(render_scale, math.sqrt(MAX_FOCUS_PIXELS / area))

    return (s, render_scale)


class FocusOverlay(Gtk.Box):
    __gtype_name__ = "InteraktivFocusOverlay"

    def __init__(self, reader):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.reader = reader
        self.add_css_class("focus-overlay")

        self.activity: Optional[Activity] = None
        self.part_index: int = 0
        self.sub_index: int = -1
        self.zoom_bias: float = 1.0
        self.focus_scale: float = 1.0
        self.is_active: bool = False

        self._last_rendered_region: Optional[Rect] = None
        self._current_key: Optional[Tuple] = None
        self._resize_source: int = 0

        self._build_ui()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        # Top bar / chrome
        bar = Gtk.CenterBox()
        bar.add_css_class("focus-bar")

        # Titles (left)
        titles = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        titles.add_css_class("focus-titles")

        self.focus_label = Gtk.Label(label="Etkinlik")
        self.focus_label.add_css_class("heading")

        self.focus_headline = Gtk.Label(label="")
        self.focus_headline.add_css_class("caption")
        self.focus_headline.add_css_class("dim-label")
        self.focus_headline.set_ellipsize(Pango.EllipsizeMode.END)
        self.focus_headline.set_max_width_chars(50)
        self.focus_headline.set_xalign(0.0)

        titles.append(self.focus_label)
        titles.append(self.focus_headline)
        bar.set_start_widget(titles)

        # Controls (right)
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls.add_css_class("focus-controls")

        # Sub-item (question) navigation
        self.btn_sub_prev = self._button(
            "go-up-symbolic", "Önceki soru (↑)", lambda: self.step_sub_item(-1)
        )
        self.lbl_sub = Gtk.Label(label="Numaralı soru yok")
        self.lbl_sub.add_css_class("caption-heading")
        self.lbl_sub.add_css_class("focus-sub-label")

        self.btn_sub_next = self._button(
            "go-down-symbolic", "Sonraki soru (↓)", lambda: self.step_sub_item(1)
        )

        controls.append(self.btn_sub_prev)
        controls.append(self.lbl_sub)
        controls.append(self.btn_sub_next)
        controls.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Activity navigation
        self.btn_prev = self._button(
            icons.PREV_PAGE, "Önceki etkinlik (← / P)", lambda: self.step_activity(-1)
        )
        self.lbl_counter = Gtk.Label(label="%100")
        self.lbl_counter.add_css_class("caption-heading")
        self.lbl_counter.add_css_class("focus-counter")

        self.btn_next = self._button(
            icons.NEXT_PAGE, "Sonraki etkinlik (→ / N)", lambda: self.step_activity(1)
        )

        controls.append(self.btn_prev)
        controls.append(self.lbl_counter)
        controls.append(self.btn_next)
        controls.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Close
        self.btn_close = self._button(
            icons.CANCEL, "Odaktan çık (Esc)", self.exit
        )
        controls.append(self.btn_close)
        bar.set_end_widget(controls)

        self.append(bar)

        # Stage (canvas host)
        self.scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True,
            vexpand=True,
        )
        self.scroller.add_css_class("focus-stage")

        self.page_view = PageView()
        self.page_view.set_halign(Gtk.Align.CENTER)
        self.page_view.set_valign(Gtk.Align.CENTER)
        self.scroller.set_child(self.page_view)

        # Clicking outside the page on the stage background exits focus
        self._stage_press = None
        self._stage_touch = False
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_stage_pressed)
        click.connect("released", self._on_stage_clicked)
        self.scroller.add_controller(click)

        # A flick sideways steps to the next activity, which is the same thing
        # the arrows in the bar do and the only one of the two that is within
        # reach of a teacher standing at the board. The crop fills the stage,
        # so there is nothing here to pan and nothing to hand the drag back to.
        SwipeNavigator(self, lambda step: self.step_activity(step))

        # Scroll / pinch nudging zoom
        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll.connect("scroll", self._on_stage_scroll)
        self.scroller.add_controller(scroll)

        # Window resize listener
        self.scroller.connect("notify::width", self._on_stage_resized)
        self.scroller.connect("notify::height", self._on_stage_resized)

        self.append(self.scroller)

    def _button(self, icon_name: str, tooltip: str, action) -> Gtk.Button:
        btn = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip)
        btn.add_css_class("tool-btn")
        bind_touch_tooltip(btn)
        if action is not None:
            btn.connect("clicked", lambda *_: action())
        return btn

    # -------------------------------------------------------- Lifecycle

    def start(self, activity: Activity, part_index: int = 0, sub_index: int = -1) -> None:
        self.is_active = True
        self.activity = activity
        self.part_index = max(0, part_index)
        self.sub_index = sub_index
        self.zoom_bias = 1.0

        self.set_visible(True)
        self.update_chrome()
        self.render_focus()

    def exit(self) -> None:
        if not self.is_active:
            return
        self.reader.exit_focus()

    def clear(self) -> None:
        self.is_active = False
        self.activity = None
        self.part_index = 0
        self.sub_index = -1
        self.zoom_bias = 1.0
        self._last_rendered_region = None
        self._current_key = None
        if self._resize_source:
            GLib.source_remove(self._resize_source)
            self._resize_source = 0
        self.page_view.clear_texture()
        self.set_visible(False)

    # --------------------------------------------------- Region & Scale

    def get_current_region(self) -> Rect:
        act = self.activity
        if act is None:
            return (0.0, 0.0, 1.0, 1.0)
        if 0 <= self.sub_index < len(act.items):
            return act.items[self.sub_index].rect
        parts = act.piece_rects
        idx = min(max(0, self.part_index), len(parts) - 1)
        return parts[idx]

    def render_focus(self) -> None:
        if not self.is_active or self.activity is None:
            return
        service = self.reader.session.service
        if service is None:
            return

        region = self.get_current_region()
        region_w = abs(region[2] - region[0])
        region_h = abs(region[3] - region[1])
        if region_w <= 0 or region_h <= 0:
            return

        # If region changed, clear stale texture so we don't display old pixels
        if self._last_rendered_region != region:
            self.page_view.clear_texture()
            self._last_rendered_region = region

        stage_w = self.scroller.get_width()
        stage_h = self.scroller.get_height()
        if stage_w <= 56 or stage_h <= 56:
            stage_w = max(stage_w, 800)
            stage_h = max(stage_h, 600)

        rot = self.reader.rotation % 360
        scale_factor = self.get_scale_factor()
        s, render_scale = calculate_focus_scale(
            stage_w, stage_h, region_w, region_h,
            rotation=rot, zoom_bias=self.zoom_bias, scale_factor=scale_factor,
        )
        self.focus_scale = s

        dev_w = region_h if rot % 180 == 90 else region_w
        dev_h = region_w if rot % 180 == 90 else region_h
        css_w = max(1.0, round(dev_w * s))
        css_h = max(1.0, round(dev_h * s))

        # Size the PageView widget
        self.page_view.set_layout_size(css_w, css_h)
        self.page_view.set_size_request(int(css_w), int(css_h))

        # Set page and rotation
        self.page_view.set_page(self.activity.page_num, rot)
        if self.reader.session.info:
            pw, ph = self.reader.session.info.size(self.activity.page_num)
            self.page_view.set_pdf_size(pw, ph)

        # Submit render request
        req = service.submit(
            page=self.activity.page_num,
            scale=render_scale,
            rotation=rot,
            clip=region,
            lane=0,
            generation=service.generation,
        )
        if req is not None:
            self._current_key = req.key

        self.update_chrome()

    def update_chrome(self) -> None:
        act = self.activity
        if act is None:
            return
        name = activity_label(act) or act.name()
        self.focus_label.set_label(f"Sayfa {act.page_num} · {name}")
        self.focus_headline.set_label(act.headline or "")

        has_items = len(act.items) > 0
        self.btn_sub_prev.set_sensitive(has_items and self.sub_index > -1)
        self.btn_sub_next.set_sensitive(has_items and self.sub_index < len(act.items) - 1)

        if not has_items:
            self.lbl_sub.set_label("Numaralı soru yok")
        elif self.sub_index >= 0:
            q_label = act.items[self.sub_index].label or str(self.sub_index + 1)
            self.lbl_sub.set_label(f"Soru {q_label} / {len(act.items)}")
        else:
            self.lbl_sub.set_label(f"{len(act.items)} soru")

        self.lbl_counter.set_label(f"%{round(self.focus_scale * 100)}")

    # ------------------------------------------------------- Navigation

    def step_activity(self, direction: int) -> None:
        if not self.is_active or self.activity is None:
            return
        next_act = self.reader.step_activity_from(self.activity, direction)
        if next_act is None:
            return

        if next_act.page_num != self.reader.current_page:
            self.reader.go_to_page(next_act.page_num)

        self.activity = next_act
        self.part_index = 0
        self.sub_index = -1
        self.zoom_bias = 1.0

        self.render_focus()

    def step_sub_item(self, direction: int) -> None:
        if not self.is_active or self.activity is None:
            return
        act = self.activity
        if not act.items:
            return

        next_sub = max(-1, min(len(act.items) - 1, self.sub_index + direction))
        if next_sub == self.sub_index:
            return

        if next_sub >= 0 and next_sub < len(act.items):
            self.part_index = act.items[next_sub].part_index

        self.sub_index = next_sub
        self.zoom_bias = 1.0
        self.render_focus()

    def nudge_zoom(self, delta: float) -> None:
        if not self.is_active:
            return
        self.zoom_bias = max(0.5, min(3.0, self.zoom_bias + delta))
        self.render_focus()

    def reset_zoom(self) -> None:
        if not self.is_active:
            return
        self.zoom_bias = 1.0
        self.render_focus()

    # ----------------------------------------------------------- Events

    def on_result(self, result) -> None:
        if not self.is_active:
            return
        if self._current_key is not None and result.key != self._current_key:
            return

        transform = PageTransform(
            page_w=result.page_w,
            page_h=result.page_h,
            scale=result.scale,
            rotation=result.request.rotation,
            origin_x=result.origin_x,
            origin_y=result.origin_y,
        )
        self.page_view.set_crop_transform(transform)
        self.page_view.set_texture(
            result.texture, result.request.page, result.request.rotation
        )

    def _on_stage_pressed(self, gesture, _n_press: int, x: float, y: float) -> None:
        self._stage_press = (x, y)
        self._stage_touch = is_touch(gesture)

    def _on_stage_clicked(self, gesture, n_press: int, x: float, y: float) -> None:
        press, self._stage_press = self._stage_press, None
        if n_press != 1:
            return
        # A finger that travelled was swiping to the next activity or steadying
        # itself on the board; only a tap on the background means "leave".
        if press is not None and self._stage_touch:
            if max(abs(x - press[0]), abs(y - press[1])) > TAP_SLOP:
                return
        # If click is outside the PageView, exit focus
        child = self.scroller.get_child()
        if child is not None:
            success, point = child.compute_point(self.scroller, Gdk.Point(x=0, y=0))
            # If coordinates are outside child bounds
            w = child.get_width()
            h = child.get_height()
            cx = child.get_allocation().x
            cy = child.get_allocation().y
            if not (cx <= x <= cx + w and cy <= y <= cy + h):
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self.exit()

    def _on_stage_scroll(self, _controller, _dx: float, dy: float) -> bool:
        if not self.is_active:
            return False
        delta = -0.15 if dy > 0 else 0.15
        self.nudge_zoom(delta)
        return True

    def _on_stage_resized(self, *_args) -> None:
        if not self.is_active:
            return
        if self._resize_source:
            GLib.source_remove(self._resize_source)
        self._resize_source = GLib.timeout_add(150, self._debounced_resize)

    def _debounced_resize(self) -> bool:
        self._resize_source = 0
        if self.is_active:
            self.render_focus()
        return GLib.SOURCE_REMOVE
