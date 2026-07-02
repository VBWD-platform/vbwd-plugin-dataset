"""Capped spreadsheet preview for a dataset snapshot (S110 T8).

Turns a *lazy* byte-chunk stream (from the storage backend) into a structured
``{"columns": [...], "rows": [[...], ...]}`` preview of at most ``max_rows`` data
rows. The cap is enforced **at read time**: the builder stops pulling chunks from
the stream the moment it has the header line plus ``max_rows`` rows, so a large
file is never fully loaded to slice its first 100 rows.

CSV is the MVP format; parsing is defensive about the delimiter (sniffed from the
header) and the encoding (decoded with ``errors="replace"`` so a stray byte can
never crash the preview).
"""
import csv
from typing import Dict, Iterable, List

DEFAULT_MAX_ROWS = 100

# Delimiters the header sniffer will consider before falling back to a comma.
_CANDIDATE_DELIMITERS = ",;\t|"

# A newline is the row separator we split the incoming byte stream on.
_ROW_SEPARATOR = "\n"


def _sniff_delimiter(header_line: str) -> str:
    """Best-effort detect the CSV delimiter from the header (default comma)."""
    try:
        dialect = csv.Sniffer().sniff(header_line, delimiters=_CANDIDATE_DELIMITERS)
        return dialect.delimiter
    except csv.Error:
        return ","


def _collect_lines(byte_chunks: Iterable[bytes], needed: int) -> List[str]:
    """Pull just enough text lines (header + rows) from the byte stream.

    Stops consuming ``byte_chunks`` as soon as ``needed`` lines are available, so
    the underlying file read never runs past the capped preview window.
    """
    lines: List[str] = []
    buffer = ""
    for chunk in byte_chunks:
        buffer += chunk.decode("utf-8", errors="replace")
        while _ROW_SEPARATOR in buffer and len(lines) < needed:
            line, buffer = buffer.split(_ROW_SEPARATOR, 1)
            lines.append(line)
        if len(lines) >= needed:
            return lines
    # The stream ended before the cap — keep a trailing unterminated line.
    if buffer and len(lines) < needed:
        lines.append(buffer)
    return lines


def build_preview(
    byte_chunks: Iterable[bytes], max_rows: int = DEFAULT_MAX_ROWS
) -> Dict[str, List]:
    """Return a ``{"columns", "rows"}`` preview capped at ``max_rows`` rows."""
    lines = _collect_lines(byte_chunks, needed=max_rows + 1)
    if not lines:
        return {"columns": [], "rows": []}

    delimiter = _sniff_delimiter(lines[0])
    parsed = list(csv.reader(lines, delimiter=delimiter))
    columns = parsed[0] if parsed else []
    rows = parsed[1 : max_rows + 1]
    return {"columns": columns, "rows": rows}
