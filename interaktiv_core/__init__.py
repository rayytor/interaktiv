"""
Everything the Interaktiv readers agree on, with no user interface in it.

The catalogue, the downloads and the on-disk layout live in `books_manager`;
this package is the layer above that: what a bake means, what the publisher's
manifest means, how the two are joined, and how a rect on a page becomes a rect
on a screen. It is imported by the GTK School Edition reader and by the bake
pipeline, and it deliberately pulls in neither GTK nor the scanner.
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
