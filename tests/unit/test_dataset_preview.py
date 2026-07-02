"""T8 — the spreadsheet preview builder caps rows server-side, at read time.

``build_preview`` turns a lazy byte-chunk stream into ``{columns, rows}`` and
must never materialise more than ``max_rows`` data rows — it stops pulling
chunks from the stream once it has the header + ``max_rows`` lines, so a huge
file is never fully loaded to slice the first 100 rows.
"""
from plugins.dataset.dataset.services.dataset_preview import (
    DEFAULT_MAX_ROWS,
    build_preview,
)


def _chunks(text, size=8):
    """Yield ``text`` as small byte chunks (a stand-in for a file stream)."""
    raw = text.encode("utf-8")
    for start in range(0, len(raw), size):
        yield raw[start : start + size]


def test_parses_header_and_rows():
    csv_text = "city,aqi\nBerlin,42\nParis,55\n"
    preview = build_preview(_chunks(csv_text))
    assert preview["columns"] == ["city", "aqi"]
    assert preview["rows"] == [["Berlin", "42"], ["Paris", "55"]]


def test_caps_at_max_rows_for_a_large_file():
    lines = ["col_a,col_b"] + [f"{index},{index * 2}" for index in range(500)]
    preview = build_preview(_chunks("\n".join(lines) + "\n"), max_rows=100)
    assert preview["columns"] == ["col_a", "col_b"]
    assert len(preview["rows"]) == 100
    # The first data row is row 0 and the last kept row is row 99 (server cap).
    assert preview["rows"][0] == ["0", "0"]
    assert preview["rows"][-1] == ["99", "198"]


def test_stops_consuming_the_stream_once_capped():
    """The stream is only pulled until the cap is reached (partial read)."""
    pulled = {"chunks": 0}

    def counting_stream():
        # One header line, then far more rows than the cap, chunk by chunk.
        yield b"h1,h2\n"
        for index in range(10_000):
            pulled["chunks"] += 1
            yield f"{index},x\n".encode("utf-8")

    preview = build_preview(counting_stream(), max_rows=10)
    assert len(preview["rows"]) == 10
    # We must not have drained anywhere near all 10k row-chunks to get 10 rows.
    assert pulled["chunks"] < 100


def test_default_cap_is_100():
    assert DEFAULT_MAX_ROWS == 100


def test_sniffs_semicolon_delimiter():
    preview = build_preview(_chunks("a;b;c\n1;2;3\n"))
    assert preview["columns"] == ["a", "b", "c"]
    assert preview["rows"] == [["1", "2", "3"]]


def test_defensive_on_non_utf8_bytes():
    # A stray latin-1 byte must not crash the preview (replace, don't raise).
    stream = iter([b"name,note\n", b"Caf\xe9,ok\n"])
    preview = build_preview(stream)
    assert preview["columns"] == ["name", "note"]
    assert len(preview["rows"]) == 1


def test_empty_stream_yields_empty_preview():
    preview = build_preview(iter([]))
    assert preview == {"columns": [], "rows": []}
