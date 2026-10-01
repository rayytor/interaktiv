"""
Everything the Rayyan Ekitap readers agree on, with no user interface in it.

The catalogue and downloads (`catalogue`), what a bake means (`regions`),
what the publisher's manifest means (`oges`), how the two are joined
(`linking`), and how a rect on a page becomes a rect on a screen
(`geometry`). Imported by the reader and by the bake pipeline; it pulls in
neither GTK nor the scanner.
"""

from .geometry import PageTransform
from .oges import Oge, OgeIndex
from .regions import BAKE_VERSION, Activity, Item, PageRegions, RegionsBook

__all__ = [
    "PageTransform",
    "Oge",
    "OgeIndex",
    "BAKE_VERSION",
    "Activity",
    "Item",
    "PageRegions",
    "RegionsBook",
]
