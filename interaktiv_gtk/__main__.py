"""`python3 -m interaktiv_gtk`: start the reader."""

import argparse
import os
import sys
import traceback

# Running from a source checkout or the board bundle, so that `interaktiv_core`
# imports whether or not the project is installed.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="interaktiv_gtk",
        description="Rayyan Ekitap, a textbook reader for classroom smart boards.",
    )
    parser.add_argument(
        "--edition",
        choices=["school"],
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
        help="Where fetched interactive activities are cached. "
             "Default: $XDG_CACHE_HOME/interaktiv/activities",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show page sizes and render times in the reader's status bar.",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Report whether this machine has what the reader needs, and exit.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    from . import TOOLKIT_ERROR, failure

    if args.selftest:
        from . import selftest

        return selftest.run()

    failure.start_log()
    if TOOLKIT_ERROR is not None:
        failure.report(
            "GTK 4 ve libadwaita bulunamadı.", f"{type(TOOLKIT_ERROR).__name__}: {TOOLKIT_ERROR}"
        )
        return 1

    try:
        from .app import InteraktivApp

        app = InteraktivApp(
            edition=args.edition,
            library_dir=args.library,
            activities_cache_dir=args.activities_cache,
            debug=args.debug,
        )
    except Exception as error:
        failure.report(f"{type(error).__name__}: {error}", traceback.format_exc())
        return 1
    return app.run([])


if __name__ == "__main__":
    raise SystemExit(main())
