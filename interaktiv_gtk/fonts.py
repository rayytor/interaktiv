"""
The app's own typeface, loaded without installing it.

Inter ships in `fonts/` and is registered with fontconfig for this process
only, so the text looks the same on every board whatever fonts it has, and
nothing is written to the user's font folder. If fontconfig cannot be reached
the stylesheet's fallback families are used instead.
"""

import ctypes
import ctypes.util
import os

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


def register() -> bool:
    """Make the bundled fonts available to this process. Call before any text is drawn."""
    if not os.path.isdir(FONT_DIR):
        return False
    try:
        library = ctypes.util.find_library("fontconfig") or "libfontconfig.so.1"
        fontconfig = ctypes.CDLL(library)
        fontconfig.FcConfigAppFontAddDir.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        fontconfig.FcConfigAppFontAddDir.restype = ctypes.c_int
        # A null config is fontconfig's current one, which is the one Pango uses.
        return bool(fontconfig.FcConfigAppFontAddDir(None, FONT_DIR.encode("utf-8")))
    except (OSError, AttributeError):
        return False
