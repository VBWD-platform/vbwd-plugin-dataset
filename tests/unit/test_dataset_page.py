"""Streaming pagination for the snapshot spreadsheet (``build_page``).

``build_page`` returns an ``offset``/``limit`` window of data rows plus a
``has_more`` flag, and — like ``build_preview`` — must never read past the
requested window: header + ``offset`` skipped rows + ``limit`` rows + ONE peek
line, then STOP. A tremendous file is never fully consumed to serve a page and
no full row count is ever computed.
"""
import pytest

from plugins.dataset.dataset.services.dataset_preview import build_page


def _chunks(text, size=8):
    """Yield ``text`` as small byte chunks (a stand-in for a file stream)."""
    raw = text.encode("utf-8")
    for start in range(0, len(raw), size):
        yield raw[start : start + size]


def _csv(row_count):
    lines = ["col_a,col_b"] + [f"{index},{index * 2}" for index in range(row_count)]
    return "\n".join(lines) + "\n"


def test_first_page_returns_header_offset_limit_and_rows():
    page = build_page(_chunks(_csv(10)), offset=0, limit=3)
    assert page["columns"] == ["col_a", "col_b"]
    assert page["rows"] == [["0", "0"], ["1", "2"], ["2", "4"]]
    assert page["offset"] == 0
    assert page["limit"] == 3
    assert page["has_more"] is True


def test_offset_skips_the_leading_rows():
    page = build_page(_chunks(_csv(10)), offset=3, limit=3)
    assert page["rows"] == [["3", "6"], ["4", "8"], ["5", "10"]]
    assert page["offset"] == 3
    assert page["has_more"] is True


def test_has_more_is_false_on_the_last_full_page():
    # 6 rows, page size 3 → the second page is the last, exactly filled.
    page = build_page(_chunks(_csv(6)), offset=3, limit=3)
    assert page["rows"] == [["3", "6"], ["4", "8"], ["5", "10"]]
    assert page["has_more"] is False


def test_has_more_is_false_on_a_short_final_page():
    # 5 rows, page size 3 → the second page has only 2 rows and no more.
    page = build_page(_chunks(_csv(5)), offset=3, limit=3)
    assert page["rows"] == [["3", "6"], ["4", "8"]]
    assert page["has_more"] is False


def test_offset_past_the_end_yields_no_rows():
    page = build_page(_chunks(_csv(3)), offset=10, limit=3)
    assert page["rows"] == []
    assert page["has_more"] is False


def test_negative_offset_and_limit_are_clamped():
    page = build_page(_chunks(_csv(10)), offset=-5, limit=-1)
    assert page["offset"] == 0
    assert page["limit"] == 0
    assert page["rows"] == []
    # limit 0 collects nothing but still peeks — there ARE more rows.
    assert page["has_more"] is True


def test_empty_stream_yields_an_empty_page():
    page = build_page(iter([]), offset=0, limit=10)
    assert page["columns"] == []
    assert page["rows"] == []
    assert page["has_more"] is False


def test_streaming_stops_after_the_window_and_peek():
    """The stream is only pulled for header + offset + limit + one peek line."""
    over_read_guard = {"pulled": 0}

    def guarded_stream():
        yield b"h1,h2\n"
        for index in range(10_000):
            over_read_guard["pulled"] += 1
            if over_read_guard["pulled"] > 50:
                raise AssertionError("build_page over-read the stream")
            yield f"{index},x\n".encode("utf-8")

    page = build_page(guarded_stream(), offset=5, limit=10)
    assert len(page["rows"]) == 10
    assert page["rows"][0] == ["5", "x"]
    # header(0) + skip 5 + collect 10 + peek 1 = 16 row-chunks pulled, well < 50.
    assert over_read_guard["pulled"] == 16


def test_page_does_not_compute_a_full_row_count():
    """A giant file must not be counted end-to-end just to paginate."""
    over_read_guard = {"pulled": 0}

    def guarded_stream():
        yield b"h\n"
        for index in range(1_000_000):
            over_read_guard["pulled"] += 1
            if over_read_guard["pulled"] > 200:
                raise AssertionError("build_page read the whole file for a count")
            yield f"{index}\n".encode("utf-8")

    page = build_page(guarded_stream(), offset=0, limit=100)
    assert page["has_more"] is True
    assert len(page["rows"]) == 100


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
