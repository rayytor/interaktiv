"""
`python3 -m interaktiv_gtk` -- the native School Edition.

The flags mirror `main.py`'s, minus everything about a port and a browser:
there is no server and no Chromium to point at one.
"""

import argparse
import os
import sys

# Running from a source checkout, so that `books_manager` and `interaktiv_core`
# import whether or not the project is installed.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="interaktiv_gtk",
        description="Interaktiv PDF Reader - School Edition (GTK4 / libadwaita)",
    )
    parser.add_argument(
        "--edition",
        choices=["full", "school"],
        default="school",
        help="Which catalogue to read. Default: school.",
    )
    parser.add_argument(
        "--library",
        default=None,
        help="Path to a packaged library (the directory holding manifest.json). "
             "Default: ./library if one is there.",
    )
    parser.add_argument(
        "--activities-cache",
        default=None,
        help="Where JIT-fetched interactive activities are cached. "
             "Default: $XDG_CACHE_HOME/interaktiv/activities",
    )
    args = parser.parse_args(argv)

    from .app import InteraktivApp

    app = InteraktivApp(
        edition=args.edition,
        library_dir=args.library,
        activities_cache_dir=args.activities_cache,
    )
    return app.run([])


if __name__ == "__main__":
    raise SystemExit(main())
