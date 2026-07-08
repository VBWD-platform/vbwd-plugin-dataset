"""AWS S3 archive backend — an OPTIONAL dataset storage backend (S110 T3).

Local is the MVP default (see ``local_backend.py``); this adapter lets some
snapshots live in S3 behind the SAME ``IDatasetStorageBackend`` port, selected
per snapshot by ``DatasetSnapshot.storage_backend``.

Design (binding constraints honoured):

* **boto3 is an OPTIONAL import** — importing this module never requires boto3;
  the client is only built lazily in :func:`build_aws_backend` when AWS is
  configured. A thin injectable ``s3_client`` seam lets tests exercise the
  adapter against a fake client (boto3/moto are not in the test image), so real
  S3 is never touched here — real-S3 verification is a DevOps step.
* **No hardcoded secrets** — bucket/prefix/region/credentials come from the
  plugin config (populated from ``var`` / ``var/secrets`` by ops), never from
  the image or repo.
* **Graceful degradation (Liskov)** — a missing/invalid secret makes
  :func:`build_aws_backend` return ``None`` (AWS off) rather than raise, so local
  keeps working and callers of the port never crash. The resolver turns that
  ``None`` into a clear, catchable :class:`DatasetStorageError` only when an AWS
  snapshot is actually requested.
"""
import hashlib
import logging
import os
from typing import Any, Dict, Iterator, Optional

from plugins.dataset.dataset.models.dataset_snapshot import STORAGE_BACKEND_AWS
from plugins.dataset.dataset.services.storage.backend import (
    DEFAULT_STREAM_CHUNK_SIZE,
    DatasetStorageError,
    IDatasetStorageBackend,
    StoredSnapshot,
    sanitize_member_filename,
)

# Default key prefix under the bucket when config does not set one.
DEFAULT_PREFIX = "datasets"

_logger = logging.getLogger(__name__)


class AwsS3Backend(IDatasetStorageBackend):
    """Stores dataset snapshots as S3 objects via an injected S3 client."""

    backend_key = STORAGE_BACKEND_AWS

    def __init__(
        self, s3_client: Any, bucket: str, prefix: str = DEFAULT_PREFIX
    ) -> None:
        """Wrap an S3 client.

        Args:
            s3_client: any object exposing boto3's ``put_object`` / ``get_object``
                / ``head_object`` / ``delete_object`` (production: a real boto3
                client; tests: a dict-backed fake). Injected so no AWS call is
                made unless AWS is configured.
            bucket: the target S3 bucket (from config/``var``, never hardcoded).
            prefix: key prefix under the bucket.
        """
        self._client = s3_client
        self._bucket = bucket
        self._prefix = (prefix or "").strip("/")

    def _build_location(
        self, category_slug: str, dataset_slug: str, taken_at: str, ext: str
    ) -> str:
        """Compose the S3 object key (single home, DRY)."""
        safe_category = category_slug or "uncategorized"
        parts = [part for part in (self._prefix, safe_category, dataset_slug) if part]
        return "/".join(parts) + f"/{taken_at}.{ext}"

    def put(
        self,
        category_slug: str,
        dataset_slug: str,
        taken_at: str,
        ext: str,
        data: bytes,
    ) -> StoredSnapshot:
        location = self._build_location(category_slug, dataset_slug, taken_at, ext)
        try:
            self._client.put_object(Bucket=self._bucket, Key=location, Body=data)
        except Exception as put_error:  # noqa: BLE001 — surface as a port error
            raise DatasetStorageError(
                f"AWS S3 put failed for '{location}': {put_error}"
            ) from put_error
        return StoredSnapshot(
            location=location,
            size_bytes=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            ext=ext,
        )

    def put_member(
        self,
        category_slug: str,
        dataset_slug: str,
        taken_at: str,
        filename: str,
        data: bytes,
    ) -> StoredSnapshot:
        safe_category = category_slug or "uncategorized"
        safe_filename = sanitize_member_filename(filename)
        parts = [
            part
            for part in (self._prefix, safe_category, dataset_slug, taken_at)
            if part
        ]
        location = "/".join(parts) + f"/{safe_filename}"
        try:
            self._client.put_object(Bucket=self._bucket, Key=location, Body=data)
        except Exception as put_error:  # noqa: BLE001 — surface as a port error
            raise DatasetStorageError(
                f"AWS S3 put_member failed for '{location}': {put_error}"
            ) from put_error
        return StoredSnapshot(
            location=location,
            size_bytes=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            ext=os.path.splitext(safe_filename)[1].lstrip(".").lower(),
        )

    def open(self, location: str) -> bytes:
        return self._read_object(location).read()

    def open_stream(
        self, location: str, chunk_size: int = DEFAULT_STREAM_CHUNK_SIZE
    ) -> Iterator[bytes]:
        """Yield the object's bytes lazily via the S3 streaming body.

        boto3's ``get_object`` returns a ``StreamingBody`` whose ``read(n)`` pulls
        only the requested bytes, so a consumer that stops early (the capped
        preview) never downloads the whole object.
        """
        body = self._read_object(location)
        while True:
            chunk = body.read(chunk_size)
            if not chunk:
                break
            yield chunk

    def _read_object(self, location: str) -> Any:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=location)
        except Exception as get_error:  # noqa: BLE001 — surface as a port error
            raise DatasetStorageError(
                f"AWS S3 get failed for '{location}': {get_error}"
            ) from get_error
        return response["Body"]

    def exists(self, location: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=location)
            return True
        except Exception:  # noqa: BLE001 — any lookup failure means "absent"
            return False

    def delete(self, location: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=location)
        except Exception as delete_error:  # noqa: BLE001 — surface as a port error
            raise DatasetStorageError(
                f"AWS S3 delete failed for '{location}': {delete_error}"
            ) from delete_error


def build_aws_backend(
    config: Optional[Dict[str, Any]],
    s3_client: Optional[Any] = None,
) -> Optional[AwsS3Backend]:
    """Build an :class:`AwsS3Backend` from config, or ``None`` if unavailable.

    Returns ``None`` (never raises) when AWS is disabled, the bucket is missing,
    boto3 is absent, or the credentials are absent — so a missing/invalid secret
    degrades gracefully (AWS off; local keeps serving). Ops sets the ``aws``
    block (bucket/prefix/region + credentials) in the plugin config / ``var``,
    never in the image or repo.
    """
    aws_config = (config or {}).get("aws") or {}
    if not aws_config.get("enabled"):
        return None
    bucket = aws_config.get("bucket")
    if not bucket:
        return None
    client = s3_client if s3_client is not None else _build_boto3_client(aws_config)
    if client is None:
        return None
    return AwsS3Backend(client, bucket, aws_config.get("prefix", DEFAULT_PREFIX))


def _build_boto3_client(aws_config: Dict[str, Any]) -> Optional[Any]:
    """Lazily build a boto3 S3 client from config credentials, or ``None``.

    boto3 is an optional dependency: if it is not installed, or the credentials
    are absent, this returns ``None`` so the AWS backend stays off without
    crashing the plugin. Credentials come from the config ``aws`` block (fed from
    ``var`` / ``var/secrets`` by ops), never hardcoded.
    """
    access_key_id = aws_config.get("aws_access_key_id")
    secret_access_key = aws_config.get("aws_secret_access_key")
    if not access_key_id or not secret_access_key:
        _logger.warning("[dataset] AWS backend disabled: credentials not configured")
        return None
    try:
        import boto3
    except ImportError:
        _logger.warning("[dataset] AWS backend disabled: boto3 is not installed")
        return None
    return boto3.client(
        "s3",
        region_name=aws_config.get("region") or None,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        aws_session_token=aws_config.get("aws_session_token") or None,
    )
