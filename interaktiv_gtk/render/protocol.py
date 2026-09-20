"""
The wire between the reader and its renderer.

A length-prefixed JSON header, optionally followed by raw pixel bytes. There is
no pickling in either direction on purpose: the only thing that crosses is data
the reader already knows how to describe, and a protocol that cannot carry code
is one less thing to reason about when the child is restarted after a crash.

Both sides speak this; the parent imports it from the main loop's thread pool
and the child imports it with no GTK loaded at all.
"""

import json
import struct

HEADER = struct.Struct("<I")


def write_message(stream, header: dict, payload: bytes = b"") -> None:
    if payload:
        header = dict(header, nbytes=len(payload))
    raw = json.dumps(header).encode("utf-8")
    stream.write(HEADER.pack(len(raw)))
    stream.write(raw)
    if payload:
        stream.write(payload)
    stream.flush()


def _read_exactly(stream, count: int) -> bytes:
    chunks = []
    got = 0
    while got < count:
        chunk = stream.read(count - got)
        if not chunk:
            raise EOFError("renderer closed the pipe")
        chunks.append(chunk)
        got += len(chunk)
    return chunks[0] if len(chunks) == 1 else b"".join(chunks)


def read_message(stream):
    """Returns `(header, payload)`. Raises `EOFError` when the peer is gone."""
    size = HEADER.unpack(_read_exactly(stream, HEADER.size))[0]
    header = json.loads(_read_exactly(stream, size).decode("utf-8"))
    nbytes = header.get("nbytes", 0)
    payload = _read_exactly(stream, nbytes) if nbytes else b""
    return header, payload
