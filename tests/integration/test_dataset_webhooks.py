"""T9 — protected inbound ingest webhooks (HMAC-signed, replay-guarded).

Exercises the real Flask app + PostgreSQL (rolled back per test):

* a valid per-source signature ingests a snapshot, advances ``last`` and fires
  ``dataset.updated``;
* a bad/absent signature → 401; a stale/replayed timestamp → 401;
* an unknown dataset (with a valid signature) → 404.

The signature covers ``"<timestamp>." + body`` using the core HMAC helper — the
same primitive the outbound webhook relay uses — so no crypto is reinvented.
"""
import json
import time
from uuid import uuid4

import pytest

import plugins.dataset as dataset_pkg
from vbwd.events.bus import event_bus
from vbwd.webhooks.signing import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    compute_signature,
)

from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.repositories.dataset_snapshot_repository import (
    DatasetSnapshotRepository,
)

INGEST_URL = "/api/v1/dataset/webhooks/pipeline/ingest"
SECRET = "pipeline-secret-value"


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _configured_secret(monkeypatch):
    """Point the plugin config at a known per-source webhook secret.

    Ops keeps the real secret in config / ``var``; the route reads it via
    ``_current_plugin_config``, so patching that is the seam-honest way to
    provide it in a test without hardcoding anything in the plugin.
    """
    config = {
        **dataset_pkg.DEFAULT_CONFIG,
        "webhook_secrets": {"pipeline": SECRET, "aws": ""},
        "webhook_timestamp_tolerance_seconds": 300,
    }
    monkeypatch.setattr(dataset_pkg, "_current_plugin_config", lambda: config)
    return config


def _make_dataset(db, slug=None):
    dataset = Dataset()
    dataset.slug = slug or f"ds-{uuid4().hex[:8]}"
    dataset.title = "Air Quality"
    dataset.price = 100.0
    db.session.add(dataset)
    db.session.commit()
    return dataset


def _signed_request(client, *, slug, secret=SECRET, timestamp=None, taken_at=None):
    body = {
        "dataset_slug": slug,
        "content": "city,aqi\nBerlin,17\n",
        "ext": "csv",
        "category": "environment",
    }
    if taken_at:
        body["taken_at"] = taken_at
    raw = json.dumps(body).encode("utf-8")
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    signature = compute_signature(secret, f"{stamp}.".encode("utf-8") + raw)
    headers = {
        SIGNATURE_HEADER: signature,
        TIMESTAMP_HEADER: stamp,
        "Content-Type": "application/json",
    }
    return client.post(INGEST_URL, data=raw, headers=headers)


def test_valid_signature_ingests_and_advances_last(db, client):
    dataset = _make_dataset(db)
    assert dataset.last_snapshot_id is None

    captured = []
    event_bus.subscribe(
        "dataset.updated", lambda name, payload: captured.append(payload)
    )

    response = _signed_request(client, slug=dataset.slug, taken_at="2026-07-01-08-00")
    assert response.status_code == 201, response.get_data(as_text=True)
    snapshot = response.get_json()
    assert snapshot["ingested_via"] == "webhook"
    assert snapshot["taken_at"] == "2026-07-01-08-00"

    # ``last`` now points at the ingested snapshot.
    db.session.refresh(dataset)
    assert str(dataset.last_snapshot_id) == snapshot["id"]

    # The snapshot bytes were persisted through the archive.
    stored = DatasetSnapshotRepository(db.session).find_by_id(snapshot["id"])
    assert stored is not None

    # ``dataset.updated`` fired for this dataset.
    assert any(item.get("dataset_id") == str(dataset.id) for item in captured)


def test_bad_signature_is_rejected(db, client):
    dataset = _make_dataset(db)
    body = json.dumps({"dataset_slug": dataset.slug, "content": "a\n1\n"}).encode()
    stamp = str(int(time.time()))
    headers = {
        SIGNATURE_HEADER: "sha256=deadbeef",
        TIMESTAMP_HEADER: stamp,
        "Content-Type": "application/json",
    }
    response = client.post(INGEST_URL, data=body, headers=headers)
    assert response.status_code == 401


def test_absent_signature_is_rejected(db, client):
    dataset = _make_dataset(db)
    body = json.dumps({"dataset_slug": dataset.slug, "content": "a\n1\n"}).encode()
    response = client.post(
        INGEST_URL, data=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 401


def test_stale_or_replayed_timestamp_is_rejected(db, client):
    dataset = _make_dataset(db)
    # A correctly-signed request whose timestamp is well outside the tolerance —
    # the shape a captured request replayed later would have.
    old_timestamp = int(time.time()) - 4000
    response = _signed_request(client, slug=dataset.slug, timestamp=old_timestamp)
    assert response.status_code == 401


def test_unknown_dataset_returns_404(db, client):
    # Valid signature (so we pass auth) but an unknown slug → 404, not 401.
    response = _signed_request(client, slug=f"missing-{uuid4().hex[:8]}")
    assert response.status_code == 404


def test_unknown_source_is_rejected(db, client):
    dataset = _make_dataset(db)
    body = json.dumps({"dataset_slug": dataset.slug, "content": "a\n1\n"}).encode()
    stamp = str(int(time.time()))
    signature = compute_signature(SECRET, f"{stamp}.".encode("utf-8") + body)
    response = client.post(
        "/api/v1/dataset/webhooks/unknown/ingest",
        data=body,
        headers={
            SIGNATURE_HEADER: signature,
            TIMESTAMP_HEADER: stamp,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
