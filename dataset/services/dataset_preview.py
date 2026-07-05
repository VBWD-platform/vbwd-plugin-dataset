"""Capped + paginated spreadsheet preview for a dataset snapshot (S110 T8).

Turns a *lazy* byte-chunk stream (from the storage backend) into a structured
``{"columns": [...], "rows": [[...], ...]}`` view of the data. Two entry points:

* :func:`build_preview` — the first ``max_rows`` data rows (the original T8 cap).
* :func:`build_page` — an ``offset``/``limit`` window with a ``has_more`` flag,
  for server-side pagination over a *tremendous* file.

Both are streaming: they pull lines from the byte stream only until they have
what the caller asked for (plus a single peek line for ``has_more``), then STOP.
A large file is never fully loaded to slice a page, and no full row count is
computed — ``has_more`` (the peek) is the pagination signal.

CSV is the MVP format; parsing is defensive about the delimiter (sniffed from
the header) and the encoding (decoded with ``errors="replace"`` so a stray byte
can never crash the preview).
"""
import csv
from typing import Dict, Iterable, Iterator, List, Tuple

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


def _iter_lines(byte_chunks: Iterable[bytes]) -> Iterator[str]:
    """Lazily yield text lines from a byte-chunk stream (one line at a time).

    Being a generator, it only decodes/splits the next chunk when the caller asks
    for another line — so a consumer that stops early never drains the rest of
    the underlying file.
    """
    buffer = ""
    for chunk in byte_chunks:
        buffer += chunk.decode("utf-8", errors="replace")
        while _ROW_SEPARATOR in buffer:
            line, buffer = buffer.split(_ROW_SEPARATOR, 1)
            yield line
    # A file that does not end in a newline leaves a trailing unterminated line.
    if buffer:
        yield buffer


def _parse_row(line: str, delimiter: str) -> List[str]:
    """Parse a single CSV line into its cell values (quotes honoured)."""
    for row in csv.reader([line], delimiter=delimiter):
        return row
    return []


def _read_page(
    byte_chunks: Iterable[bytes], offset: int, limit: int
) -> Tuple[List[str], List[List[str]], bool]:
    """Stream one page and return ``(columns, rows, has_more)``.

    Reads the header, skips ``offset`` data rows, collects up to ``limit`` rows,
    then peeks ONE extra line for ``has_more`` — and stops. The rest of the file
    is never pulled (bounded IO + memory for a tremendous dataset).
    """
    lines = _iter_lines(byte_chunks)
    header_line = next(lines, None)
    if header_line is None:
        return [], [], False

    delimiter = _sniff_delimiter(header_line)

    # Skip the rows before the window without keeping them (bounded memory).
    for _ in range(offset):
        if next(lines, None) is None:
            break

    collected: List[str] = []
    for _ in range(limit):
        row_line = next(lines, None)
        if row_line is None:
            break
        collected.append(row_line)

    # A single peek line tells us whether another page exists — no full count.
    has_more = next(lines, None) is not None

    columns = _parse_row(header_line, delimiter)
    rows = [_parse_row(row_line, delimiter) for row_line in collected]
    return columns, rows, has_more


def build_page(
    byte_chunks: Iterable[bytes],
    offset: int = 0,
    limit: int = DEFAULT_MAX_ROWS,
) -> Dict[str, object]:
    """Return one ``offset``/``limit`` window of data rows as a page.

    Streams only the requested window plus a peek line (see :func:`_read_page`).
    ``offset``/``limit`` are clamped to non-negative values here defensively (the
    route clamps to a server max too).

    Returns ``{"columns", "rows", "offset", "limit", "has_more"}``.
    """
    offset = max(0, offset)
    limit = max(0, limit)
    columns, rows, has_more = _read_page(byte_chunks, offset, limit)
    return {
        "columns": columns,
        "rows": rows,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
    }


def build_preview(
    byte_chunks: Iterable[bytes], max_rows: int = DEFAULT_MAX_ROWS
) -> Dict[str, List]:
    """Return a ``{"columns", "rows"}`` preview capped at ``max_rows`` rows.

    Thin wrapper over :func:`_read_page` (the first page from offset 0) that keeps
    the original T8 contract — just ``columns`` + ``rows``.
    """
    columns, rows, _has_more = _read_page(byte_chunks, offset=0, limit=max_rows)
    return {"columns": columns, "rows": rows}
