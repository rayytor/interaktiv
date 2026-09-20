"""
Theming and colour matrices for Interaktiv School Edition.

Four themes, matching the web reader:
  dark -> light -> sepia -> inverted (viewer.js:239)

Canvas filters:
  - dark:     none
  - light:    none
  - sepia:    sepia(0.2) contrast(0.95)
  - inverted: invert(0.9) hue-rotate(180deg) brightness(1.05) contrast(0.95)

As documented in milestones.md:
  The canvas filters are NOT baked into the pixmap: that would be a CPU pass
  per page and would invalidate the whole texture cache on every theme switch.
  Each CSS filter primitive is a 4x4 colour matrix plus offset; we multiply the
  chain once at startup (pure Python, no numpy) into one (Graphene.Matrix, Graphene.Vec4)
  per theme and wrap only the texture node in push_color_matrix. Hotspots, chips,
  and highlights stay untinted. A theme switch is then a queue_draw(): no re-render,
  no cache invalidation.
"""

import math
from typing import Dict, List, Optional, Tuple

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Graphene, Gtk

THEMES = ["dark", "light", "sepia", "inverted"]

THEME_CSS: Dict[str, str] = {
    "sepia": """
@define-color window_bg_color #f4ecd8;
@define-color window_fg_color #3b2c1a;
@define-color view_bg_color #f4ecd8;
@define-color view_fg_color #3b2c1a;
@define-color headerbar_bg_color #eadecc;
@define-color headerbar_fg_color #3b2c1a;
@define-color headerbar_border_color #d6c6aa;
@define-color card_bg_color #dfd1b8;
@define-color card_fg_color #3b2c1a;
@define-color card_border_color #d6c6aa;
@define-color sidebar_bg_color #efe3ce;
@define-color sidebar_fg_color #3b2c1a;
@define-color sidebar_border_color #d6c6aa;
@define-color accent_color #935a28;
@define-color accent_bg_color #935a28;
@define-color accent_fg_color #ffffff;
""",
    "inverted": """
@define-color window_bg_color #0a0c10;
@define-color window_fg_color #e6edf3;
@define-color view_bg_color #0a0c10;
@define-color view_fg_color #e6edf3;
@define-color headerbar_bg_color #12151c;
@define-color headerbar_fg_color #e6edf3;
@define-color headerbar_border_color #262c3a;
@define-color card_bg_color #181d26;
@define-color card_fg_color #e6edf3;
@define-color card_border_color #262c3a;
@define-color sidebar_bg_color #0f1217;
@define-color sidebar_fg_color #e6edf3;
@define-color sidebar_border_color #262c3a;
@define-color accent_color #58a6ff;
@define-color accent_bg_color #58a6ff;
@define-color accent_fg_color #ffffff;
""",
}

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
    Apply theme to the application chrome and window:
      - Chrome: dark/inverted -> FORCE_DARK, light/sepia -> FORCE_LIGHT
      - Libadwaita named colours: dynamically loaded via Gtk.CssProvider
      - Window: toggle .theme-sepia and .theme-inverted CSS classes
    """
    global _theme_provider
    theme = theme if theme in THEMES else "dark"

    style_manager = Adw.StyleManager.get_default()
    if style_manager is not None:
        if theme in ("dark", "inverted"):
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

    display = Gdk.Display.get_default()
    if display is not None:
        if _theme_provider is None:
            _theme_provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                display, _theme_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
            )
        css_data = THEME_CSS.get(theme, "")
        if hasattr(_theme_provider, "load_from_string"):
            _theme_provider.load_from_string(css_data)
        else:
            _theme_provider.load_from_data(css_data.encode("utf-8"))

    if window is not None:
        window.remove_css_class("theme-sepia")
        window.remove_css_class("theme-inverted")
        if theme == "sepia":
            window.add_css_class("theme-sepia")
        elif theme == "inverted":
            window.add_css_class("theme-inverted")
