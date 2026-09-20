#!/usr/bin/env python3
"""
Main application launcher for Interaktiv PDF Reader.
Handles CLI arguments, port selection, browser launching, and server execution.
"""

import argparse
import os
import socket
import subprocess
import sys
import time
import webbrowser
from server import run_server


def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) != 0


def find_available_port(start_port: int = 8080, max_attempts: int = 50) -> int:
    for port in range(start_port, start_port + max_attempts):
        if is_port_available(port):
            return port
    raise RuntimeError(f"Could not find an available port in range {start_port}-{start_port + max_attempts}")


def launch_browser(url: str):
    """Attempt to launch Chrome/Chromium in standalone app mode, fallback to default browser."""
    profile_dir = os.path.expanduser("~/.config/interaktiv/chrome-profile")
    os.makedirs(profile_dir, exist_ok=True)

    flags = [
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--class=interaktiv-pdf",
        "--name=interaktiv-pdf",
        "--app-id=interaktiv-pdf",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    candidates = [
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "brave-browser",
        "microsoft-edge",
    ]

    for candidate in candidates:
        if subprocess.call(["which", candidate], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            try:
                subprocess.Popen([candidate] + flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:
                pass

    # Fallback to firefox or system default
    try:
        if subprocess.call(["which", "firefox"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            subprocess.Popen(["firefox", "--new-window", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    except Exception:
        pass

    webbrowser.open(url)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Interaktiv Lightweight PDF Reader")
    parser.add_argument(
        "pdf",
        nargs="?",
        default=None,
        help="Path to PDF file (optional; if omitted, opens dashboard)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to run the HTTP server on (default: 8080)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically launch the browser window",
    )
    parser.add_argument(
        "--auto-shutdown",
        action="store_true",
        help="Automatically shut down the server when the browser window is closed",
    )
    parser.add_argument(
        "--edition",
        choices=["full", "school"],
        default=None,
        help="Which build to run. 'school' reads a packaged library and presents "
             "the touch-friendly board UI; 'full' is the authoring reader. "
             "Default: detected from a library/manifest.json next to the app.",
    )
    parser.add_argument(
        "--library",
        default=None,
        help="Path to a packaged library (the directory holding manifest.json). "
             "Default: ./library",
    )
    parser.add_argument(
        "--activities-cache",
        default=None,
        help="Where JIT-fetched interactive activities are cached. "
             "Default: $XDG_CACHE_HOME/interaktiv/activities",
    )

    args = parser.parse_args()

    port = args.port
    if not is_port_available(port):
        new_port = find_available_port(port + 1)
        print(f"Port {port} is busy. Switched to available port {new_port}.")
        port = new_port

    base_dir = os.path.dirname(os.path.abspath(__file__))
    pdf_rel_path = os.path.relpath(os.path.abspath(args.pdf), base_dir) if args.pdf else None

    url = f"http://127.0.0.1:{port}/"

    if not args.no_browser:
        # Launch browser slightly after starting server thread, or spawn before serve
        import threading
        def _open():
            time.sleep(0.4)
            launch_browser(url)
        threading.Thread(target=_open, daemon=True).start()

    print(f"Starting Interaktiv PDF Reader on {url}...")
    run_server(
        port=port,
        default_pdf=pdf_rel_path,
        directory=base_dir,
        auto_shutdown=args.auto_shutdown,
        library_dir=args.library,
        edition=args.edition,
        activities_cache_dir=args.activities_cache,
    )


if __name__ == "__main__":
    main()
