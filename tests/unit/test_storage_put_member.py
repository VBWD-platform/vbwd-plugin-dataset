"""S124 — ``put_member`` on both storage backends (per-issue folder layout).

A companion file lands in a per-issue **folder**
``datasets/<cat>/<ds>/<taken_at>/<safe-filename>`` — distinct from the primary's
``<taken_at>.<ext>`` sibling file. The filename is sanitized so a ``..``/separator
payload can never escape the per-issue folder (the core FilesystemManager confines
the path too — defence in depth).
"""
import hashlib
import io

from vbwd.services.filesystem.memory import InMemoryFilesystemManager

from plugins.dataset.dataset.services.storage.aws_backend import AwsS3Backend
from plugins.dataset.dataset.services.storage.backend import (
    sanitize_member_filename,
)
from plugins.dataset.dataset.services.storage.local_backend import (
    ARCHIVE_PREFIX,
    LocalArchiveBackend,
)


class FakeS3Client:
    def __init__(self):
        self.store = {}

    def put_object(self, *, Bucket, Key, Body):
        self.store[(Bucket, Key)] = bytes(Body)

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.store[(Bucket, Key)])}

    def head_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.store:
            raise KeyError(Key)
        return {"ContentLength": len(self.store[(Bucket, Key)])}

    def delete_object(self, *, Bucket, Key):
        self.store.pop((Bucket, Key), None)


def _local():
    return LocalArchiveBackend(InMemoryFilesystemManager())


def _aws():
    return AwsS3Backend(FakeS3Client(), bucket="datasets-bucket", prefix="datasets")


# ----------------------------------------------------------------------
# Filename sanitization (single home, shared by both backends)
# ----------------------------------------------------------------------


def test_sanitize_strips_directory_components_and_parent_refs():
    assert sanitize_member_filename("../../etc/passwd") == "passwd"
    assert sanitize_member_filename("a/b/report.pdf") == "report.pdf"
    assert sanitize_member_filename("..") == "file"
    assert sanitize_member_filename("") == "file"
    # A legitimate name is preserved.
    assert sanitize_member_filename("chart-01.png") == "chart-01.png"


# ----------------------------------------------------------------------
# LocalArchiveBackend.put_member
# ----------------------------------------------------------------------


def test_local_put_member_uses_per_issue_folder_layout():
    backend = _local()
    stored = backend.put_member(
        category_slug="environment",
        dataset_slug="air-quality",
        taken_at="2026-07-01-09-30",
        filename="report.pdf",
        data=b"%PDF-1.4 report\n",
    )
    assert stored.location == (
        f"{ARCHIVE_PREFIX}/environment/air-quality/2026-07-01-09-30/report.pdf"
    )
    assert stored.ext == "pdf"
    assert stored.size_bytes == len(b"%PDF-1.4 report\n")


def test_local_put_member_round_trips_bytes_and_checksum():
    backend = _local()
    payload = b"chart-bytes\n"
    stored = backend.put_member("cat", "slug", "2026-07-01-10-00", "chart.png", payload)

    assert backend.exists(stored.location)
    assert backend.open(stored.location) == payload
    assert stored.checksum == hashlib.sha256(payload).hexdigest()


def test_local_put_member_rejects_path_escape():
    backend = _local()
    stored = backend.put_member(
        "environment", "air-quality", "2026-07-01-09-30", "../../secret.txt", b"x"
    )
    assert ".." not in stored.location
    assert stored.location == (
        f"{ARCHIVE_PREFIX}/environment/air-quality/2026-07-01-09-30/secret.txt"
    )


# ----------------------------------------------------------------------
# AwsS3Backend.put_member (LSP parity, via the fake client)
# ----------------------------------------------------------------------


def test_aws_put_member_uses_per_issue_folder_layout():
    backend = _aws()
    stored = backend.put_member(
        category_slug="environment",
        dataset_slug="air-quality",
        taken_at="2026-07-01-09-30",
        filename="report.pdf",
        data=b"%PDF\n",
    )
    assert stored.location == (
        "datasets/environment/air-quality/2026-07-01-09-30/report.pdf"
    )


def test_aws_put_member_round_trips_bytes():
    backend = _aws()
    payload = b"s3-member\n"
    stored = backend.put_member("cat", "slug", "2026-07-01-10-00", "a.json", payload)
    assert backend.exists(stored.location)
    assert backend.open(stored.location) == payload


def test_aws_put_member_rejects_path_escape():
    backend = _aws()
    stored = backend.put_member(
        "environment", "air-quality", "2026-07-01-09-30", "../evil.txt", b"x"
    )
    assert ".." not in stored.location
