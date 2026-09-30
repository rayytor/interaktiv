"""
The four reading themes: their colours and what each does to the page.

A theme is two things. The chrome takes a palette: a handful of named colours
that the stylesheet is written in terms of, plus libadwaita's own names mapped
onto them so that stock widgets follow. The page takes a colour matrix: sepia
and night tint the rendered sheet itself.

The page tint is not baked into the pixels. That would be a CPU pass per page
and would invalidate the texture cache on every theme switch. Each CSS filter
primitive is a 4x4 colour matrix plus an offset; the chain is multiplied once
at import into one matrix per theme, and only the texture node is wrapped in
it. Hotspots and highlights stay untinted, and a theme switch is a redraw.
"""

import math
from typing import Dict, List, Optional, Tuple

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Graphene, Gtk  # noqa: E402

THEMES = ["dark", "light", "sepia", "inverted"]

THEME_LABELS = {
    "dark": "Koyu",
    "light": "Açık",
    "sepia": "Sepya",
    "inverted": "Gece",
}

# name -> (background, surface, raised, foreground, desk, accent, on accent, activity)
PALETTES: Dict[str, Tuple[str, ...]] = {
    "dark": ("#0f141c", "#18202b", "#232d3b", "#eaf0f6", "#0a0e14",
             "#2cc4d0", "#04262b", "#ffb020"),
    "light": ("#f5f3ee", "#ffffff", "#ffffff", "#1b2330", "#e3e0d8",
              "#0a8a96", "#ffffff", "#e8930c"),
    "sepia": ("#f4ecd8", "#fbf5e6", "#fffaf0", "#3b2c1a", "#e6dcc3",
              "#935a28", "#ffffff", "#d9820a"),
    "inverted": ("#07090d", "#11151c", "#1a202a", "#d9e1ea", "#040507",
                 "#58a6ff", "#04182e", "#ffb020"),
}

DARK_THEMES = ("dark", "inverted")


def palette_css(theme: str) -> str:
    """
    The named colours for one theme.

    The `ik_*` names are the app's own and are what `style.css` uses. The rest
    are libadwaita's, set from them so that its widgets match.
    """
    bg, surface, raised, fg, desk, accent, on_accent, activity = PALETTES[theme]
    return f"""
@define-color ik_bg {bg};
@define-color ik_surface {surface};
@define-color ik_raised {raised};
@define-color ik_fg {fg};
@define-color ik_desk {desk};
@define-color ik_accent {accent};
@define-color ik_on_accent {on_accent};
@define-color ik_activity {activity};
@define-color window_bg_color {bg};
@define-color window_fg_color {fg};
@define-color view_bg_color {bg};
@define-color view_fg_color {fg};
@define-color headerbar_bg_color {bg};
@define-color headerbar_fg_color {fg};
@define-color headerbar_border_color {fg};
@define-color headerbar_backdrop_color {bg};
@define-color headerbar_shade_color alpha({fg}, 0.12);
@define-color card_bg_color {surface};
@define-color card_fg_color {fg};
@define-color card_shade_color alpha({fg}, 0.08);
@define-color popover_bg_color {raised};
@define-color popover_fg_color {fg};
@define-color dialog_bg_color {raised};
@define-color dialog_fg_color {fg};
@define-color sidebar_bg_color {surface};
@define-color sidebar_fg_color {fg};
@define-color accent_color {accent};
@define-color accent_bg_color {accent};
@define-color accent_fg_color {on_accent};
"""


THEME_CSS: Dict[str, str] = {theme: palette_css(theme) for theme in THEMES}

_theme_provider: Optional[Gtk.CssProvider] = None


# --- Pure Python Colour Matrix Math ------------------------------------------

def _mat_mult(a: List[List[float]], b: List[List[float]]) -> List[List[float]]:
    """Multiply two 4x4 matrices: a @ b."""
    res = [[0.0] * 4 for _ in range(4)]
    for i in range(4):
        for j in range(4):
            res[i][j] = sum(a[i][k] * b[k][j] for k in range(4))
    return res


def _mat_vec_mult(m: List[List[float]], v: List[float]) -> List[float]:
    """Multiply 4x4 matrix by 4-element vector: m @ v."""
    return [sum(m[i][k] * v[k] for k in range(4)) for i in range(4)]


def _compose_affine(
    m1: List[List[float]], c1: List[float],
    m2: List[List[float]], c2: List[float],
) -> Tuple[List[List[float]], List[float]]:
    """
    Compose two affine color transforms T2(T1(x)):
      T1(x) = m1 @ x + c1
      T2(T1(x)) = m2 @ (m1 @ x + c1) + c2 = (m2 @ m1) @ x + (m2 @ c1 + c2)
    """
    m = _mat_mult(m2, m1)
    m2_c1 = _mat_vec_mult(m2, c1)
    c = [m2_c1[i] + c2[i] for i in range(4)]
    return m, c


def _filter_sepia(s: float) -> Tuple[List[List[float]], List[float]]:
    """W3C CSS sepia(s) primitive."""
    m = [
        [0.393 + 0.607 * (1.0 - s), 0.769 - 0.769 * (1.0 - s), 0.189 - 0.189 * (1.0 - s), 0.0],
        [0.349 - 0.349 * (1.0 - s), 0.686 + 0.314 * (1.0 - s), 0.168 - 0.168 * (1.0 - s), 0.0],
        [0.272 - 0.272 * (1.0 - s), 0.534 - 0.534 * (1.0 - s), 0.131 + 0.869 * (1.0 - s), 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    return m, [0.0, 0.0, 0.0, 0.0]


def _filter_contrast(c: float) -> Tuple[List[List[float]], List[float]]:
    """W3C CSS contrast(c) primitive."""
    m = [
        [c, 0.0, 0.0, 0.0],
        [0.0, c, 0.0, 0.0],
        [0.0, 0.0, c, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    off = 0.5 * (1.0 - c)
    return m, [off, off, off, 0.0]


def _filter_invert(v: float) -> Tuple[List[List[float]], List[float]]:
    """W3C CSS invert(v) primitive: C' = (1 - 2v) * C + v."""
    m = [
        [1.0 - 2.0 * v, 0.0, 0.0, 0.0],
        [0.0, 1.0 - 2.0 * v, 0.0, 0.0],
        [0.0, 0.0, 1.0 - 2.0 * v, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    return m, [v, v, v, 0.0]


def _filter_brightness(b: float) -> Tuple[List[List[float]], List[float]]:
    """W3C CSS brightness(b) primitive."""
    m = [
        [b, 0.0, 0.0, 0.0],
        [0.0, b, 0.0, 0.0],
        [0.0, 0.0, b, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    return m, [0.0, 0.0, 0.0, 0.0]


def _filter_hue_rotate(deg: float) -> Tuple[List[List[float]], List[float]]:
    """W3C CSS hue-rotate(deg) primitive."""
    rad = math.radians(deg)
    cos_v = math.cos(rad)
    sin_v = math.sin(rad)
    m = [
        [0.213 + cos_v * 0.787 - sin_v * 0.213, 0.715 - cos_v * 0.715 - sin_v * 0.715, 0.072 - cos_v * 0.072 + sin_v * 0.928, 0.0],
        [0.213 - cos_v * 0.213 + sin_v * 0.143, 0.715 + cos_v * 0.285 + sin_v * 0.140, 0.072 - cos_v * 0.072 - sin_v * 0.283, 0.0],
        [0.213 - cos_v * 0.213 - sin_v * 0.787, 0.715 - cos_v * 0.715 + sin_v * 0.715, 0.072 + cos_v * 0.928 + sin_v * 0.072, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    return m, [0.0, 0.0, 0.0, 0.0]


def _to_graphene(m: List[List[float]], c: List[float]) -> Tuple[Graphene.Matrix, Graphene.Vec4]:
    """Convert row-major 4x4 matrix and 4-offset into Graphene.Matrix (column-major) and Vec4."""
    col_major = [m[r][col] for col in range(4) for r in range(4)]
    g_matrix = Graphene.Matrix()
    g_matrix.init_from_float(col_major)
    g_vec = Graphene.Vec4()
    g_vec.init(c[0], c[1], c[2], c[3])
    return g_matrix, g_vec


# --- Precomputed Colour Matrices ----------------------------------------------

def _build_theme_matrices() -> Dict[str, Tuple[Optional[Graphene.Matrix], Optional[Graphene.Vec4]]]:
    matrices: Dict[str, Tuple[Optional[Graphene.Matrix], Optional[Graphene.Vec4]]] = {
        "dark": (None, None),
        "light": (None, None),
    }

    # sepia(0.2) contrast(0.95)
    m_s, c_s = _filter_sepia(0.2)
    m_c, c_c = _filter_contrast(0.95)
    m_sepia, c_sepia = _compose_affine(m_s, c_s, m_c, c_c)
    matrices["sepia"] = _to_graphene(m_sepia, c_sepia)

    # invert(0.9) hue-rotate(180deg) brightness(1.05) contrast(0.95)
    m_inv, c_inv = _filter_invert(0.9)
    m_hr, c_hr = _filter_hue_rotate(180.0)
    m1, c1 = _compose_affine(m_inv, c_inv, m_hr, c_hr)
    m_br, c_br = _filter_brightness(1.05)
    m2, c2 = _compose_affine(m1, c1, m_br, c_br)
    m3, c3 = _compose_affine(m2, c2, m_c, c_c)
    matrices["inverted"] = _to_graphene(m3, c3)

    return matrices


THEME_MATRICES = _build_theme_matrices()


def get_theme_color_matrix(theme: str) -> Tuple[Optional[Graphene.Matrix], Optional[Graphene.Vec4]]:
    """Return the cached (Graphene.Matrix, Graphene.Vec4) for a given theme, or (None, None)."""
    return THEME_MATRICES.get(theme, (None, None))


# --- Theme Application --------------------------------------------------------

def apply_theme(theme: str, window=None) -> None:
    """
    Apply a theme to the chrome: libadwaita's light or dark scheme, the
    palette's named colours, and a `theme-<name>` class on the window.
    """
    global _theme_provider
    theme = theme if theme in THEMES else "dark"

    style_manager = Adw.StyleManager.get_default()
    if style_manager is not None:
        style_manager.set_color_scheme(
            Adw.ColorScheme.FORCE_DARK if theme in DARK_THEMES
            else Adw.ColorScheme.FORCE_LIGHT
        )

    display = Gdk.Display.get_default()
    if display is not None:
        if _theme_provider is None:
            _theme_provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                display, _theme_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
            )
        css_data = THEME_CSS[theme]
        if hasattr(_theme_provider, "load_from_string"):
            _theme_provider.load_from_string(css_data)  # floor: ok
        else:
            _theme_provider.load_from_data(css_data.encode("utf-8"))

    if window is not None:
        for name in THEMES:
            window.remove_css_class(f"theme-{name}")
        window.add_css_class(f"theme-{theme}")
