"""
The renderer, in its own process.

Run as `python3 -m interaktiv_gtk.render.worker`, reading commands on stdin and
writing headers and pixels on stdout.

**Why a process and not a thread.** PyMuPDF does not release the GIL while it
renders. A render thread inside the reader therefore does not run beside the
main loop, it runs instead of it: measured on this machine, twenty renders of
the 443 MB chemistry book held every other Python thread for 11 ms at the median
and 240 ms at the worst page, and paging through that book at hold-repeat speed
froze the window for seconds at a time. No amount of queue discipline fixes
that, because the contention is under the queue. Moving MuPDF behind a pipe
costs 80 ms of interpreter start when a book opens and 3 ms per page to carry
the pixels back -- and the 3 ms is paid on a reader thread that is blocked in
`read`, where it holds no lock the main loop wants.

It also makes the memory story simple. MuPDF's store cannot be capped in
PyMuPDF 1.28 -- `TOOLS.store_maxsize()` returns None -- so the only real
guarantee that a closed book gives its memory back is that the process holding
it exits, which is exactly what happens here.

The child renders one page at a time and answers in order. Everything about
what to render next -- coalescing, generations, priority -- stays in the parent,
where the reader already knows which page is on screen.
"""

import os
import sys


def _isolate_stdout():
    """
    Take the real stdout for the protocol and point `sys.stdout` at stderr.

    MuPDF and any stray `print` would otherwise write into the middle of a pixel
    payload, and the reader would see a page as a protocol error.
    """
    fd = os.dup(sys.stdout.fileno())
    os.close(sys.stdout.fileno())
    os.open(os.devnull, os.O_WRONLY)
    sys.stdout = sys.stderr
    return os.fdopen(fd, "wb", buffering=0)


def main() -> int:
    out = _isolate_stdout()
    stdin = sys.stdin.buffer

    from interaktiv_core import jobs

    from .protocol import read_message, write_message

    try:
        import pymupdf

        # On a board memory is scarcer than milliseconds: let MuPDF re-decode
        # rather than hold every image it has seen.
        pymupdf.TOOLS.set_low_memory(True)
        pymupdf.TOOLS.mupdf_display_errors(False)
    except Exception:
        pass

    handle = None
    renders = 0

    while True:
        try:
            header, _payload = read_message(stdin)
        except (EOFError, OSError, ValueError):
            break

        op = header.get("op")
        if op == "quit":
            break

        if op == "open":
            from .document import DocumentHandle

            try:
                handle = DocumentHandle(header["path"])
            except Exception as exc:
                write_message(out, {"op": "error", "message": str(exc)})
                continue
            write_message(out, {
                "op": "ready",
                "page_count": handle.info.page_count,
                "page_sizes": [list(s) for s in handle.info.page_sizes],
                "toc": handle.toc(),
            })
            continue

        if op == "render" and handle is not None:
            from .requests import RenderRequest

            request = RenderRequest(
                page=header["page"],
                scale=header["scale"],
                rotation=header.get("rotation", 0),
                clip=tuple(header["clip"]) if header.get("clip") else None,
            )
            try:
                data, w, h, stride, ox, oy, pw, ph, scale, ms = handle.render(request)
            except Exception as exc:
                write_message(out, {
                    "op": "failed", "token": header.get("token"), "message": str(exc),
                })
                continue
            write_message(out, {
                "op": "result",
                "token": header.get("token"),
                "width": w, "height": h, "stride": stride,
                "origin_x": ox, "origin_y": oy,
                "page_w": pw, "page_h": ph,
                "scale": scale, "ms": ms,
            }, data)
            del data

            renders += 1
            if renders % 16 == 0:
                jobs.trim_memory()
            continue

        if op == "idle":
            # The parent's queue ran dry. Nothing is waiting on this process, so
            # this is the cheapest possible moment to give memory back.
            jobs.trim_memory()
            continue

        if op == "search" and handle is not None:
            token = header.get("token")
            needle = header.get("needle", "")
            if "pages" in header:
                results = {}
                for p in header["pages"]:
                    try:
                        results[p] = handle.search(p, needle)
                    except Exception:
                        results[p] = []
                write_message(out, {
                    "op": "search-result", "token": token, "results": results,
                })
            else:
                try:
                    rects = handle.search(header["page"], needle)
                except Exception:
                    rects = []
                write_message(out, {
                    "op": "search-result", "token": token, "rects": rects,
                })
            continue

        if op == "extract-text" and handle is not None:
            token = header.get("token")
            pages = header.get("pages", [])
            texts = {}
            for p in pages:
                try:
                    texts[p] = handle.get_text(p)
                except Exception:
                    texts[p] = ""
            write_message(out, {
                "op": "text-result", "token": token, "texts": texts,
            })
            continue

    if handle is not None:
        handle.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
