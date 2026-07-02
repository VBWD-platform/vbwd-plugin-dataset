"""T3 — AwsS3Backend stores/reads a snapshot behind the shared storage port.

boto3/moto are NOT in the test image, so the S3 client is a thin injectable seam
exercised against a dict-backed fake client (no real AWS is touched). Real-S3
verification is deferred to DevOps. The adapter honours the SAME
``IDatasetStorageBackend`` contract as ``LocalArchiveBackend`` (Liskov).
"""
import hashlib
import io

from plugins.dataset.dataset.models.dataset_snapshot import STORAGE_BACKEND_AWS
from plugins.dataset.dataset.services.storage.aws_backend import AwsS3Backend


class FakeS3Client:
    """A minimal dict-backed stand-in for a boto3 S3 client.

    Implements only the four operations the backend uses. ``get_object`` returns
    a fresh ``BytesIO`` under ``Body`` (mirroring boto3's ``StreamingBody`` .read
    contract) so the streaming path works without botocore installed.
    """

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


def _backend():
    return AwsS3Backend(FakeS3Client(), bucket="datasets-bucket", prefix="datasets")


def test_backend_key_is_aws():
    assert AwsS3Backend(FakeS3Client(), bucket="b").backend_key == STORAGE_BACKEND_AWS


def test_put_uses_bucket_prefix_key_convention():
    backend = _backend()
    stored = backend.put(
        category_slug="environment",
        dataset_slug="air-quality",
        taken_at="2026-07-01-09-30",
        ext="csv",
        data=b"col-a,col-b\n1,2\n",
    )
    assert stored.location == "datasets/environment/air-quality/2026-07-01-09-30.csv"
    assert stored.ext == "csv"
    assert stored.size_bytes == len(b"col-a,col-b\n1,2\n")


def test_put_then_open_round_trips_bytes_and_checksum():
    backend = _backend()
    payload = b"hello,s3\n"
    stored = backend.put("cat", "slug", "2026-07-01-10-00", "csv", payload)

    assert backend.exists(stored.location)
    assert backend.open(stored.location) == payload
    assert stored.checksum == hashlib.sha256(payload).hexdigest()


def test_open_stream_yields_chunks():
    backend = _backend()
    payload = b"y" * 40
    stored = backend.put("cat", "slug", "2026-07-01-10-00", "bin", payload)

    chunks = list(backend.open_stream(stored.location, chunk_size=8))
    assert len(chunks) > 1
    assert b"".join(chunks) == payload


def test_delete_removes_the_stored_object():
    backend = _backend()
    stored = backend.put("cat", "slug", "2026-07-01-10-00", "csv", b"x")
    backend.delete(stored.location)
    assert not backend.exists(stored.location)
