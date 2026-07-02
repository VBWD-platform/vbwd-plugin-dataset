"""T8 — scoped read API, metered access, preview, meta, and download.

Exercises the real Flask app + PostgreSQL (rolled back per test):

* the public catalogue lists active datasets only;
* ``/data`` is API-key + scope + entitlement gated and debits a token per call;
* over quota → 429; unentitled → 403 (GDPR: no cross-user data);
* ``/preview`` caps at 100 rows even for a larger file;
* ``/meta`` returns the issue metadata;
* ``/download`` sets an attachment header (session auth, no API key).
"""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from flask import current_app

from vbwd.events.bus import event_bus
from vbwd.extensions import db as _db
from vbwd.models.enums import TokenTransactionType, UserRole, UserStatus
from vbwd.models.user import User
from vbwd.repositories.api_key_repository import ApiKeyRepository
from vbwd.services.api_key_service import ApiKeyService

from plugins.dataset import build_dataset_access_service
from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.repositories.dataset_repository import DatasetRepository
from plugins.dataset.dataset.repositories.dataset_snapshot_repository import (
    DatasetSnapshotRepository,
)
from plugins.dataset.dataset.services.dataset_service import DatasetService
from plugins.dataset.dataset.services.storage.local_backend import LocalArchiveBackend

READ_SCOPE = "dataset:read"


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(db, role=UserRole.USER):
    user = User(
        id=uuid4(),
        email=f"user-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=role,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _catalogue_service():
    backend = LocalArchiveBackend(current_app.container.filesystem_manager())
    return DatasetService(
        dataset_repository=DatasetRepository(_db.session),
        snapshot_repository=DatasetSnapshotRepository(_db.session),
        storage_backend=backend,
        event_bus=event_bus,
    )


def _make_dataset_with_snapshot(db, *, rows_csv=b"city,aqi\nBerlin,42\n", slug=None):
    dataset = Dataset()
    dataset.slug = slug or f"air-quality-{uuid4().hex[:8]}"
    dataset.title = "Air Quality"
    dataset.price = 100.0
    dataset.is_active = True
    db.session.add(dataset)
    db.session.commit()

    service = _catalogue_service()
    service.add_snapshot(
        str(dataset.id),
        data=rows_csv,
        ext="csv",
        taken_at="2026-06-01-00-00",
        category_slug="environment",
    )
    db.session.commit()
    return dataset


def _grant(db, user, dataset):
    build_dataset_access_service().grant(user.id, dataset.id, triggered_by="test")
    db.session.commit()


def _api_key(db, user, scopes=(READ_SCOPE,)):
    service = ApiKeyService(ApiKeyRepository(db.session))
    _, plaintext = service.generate(
        user_id=user.id, label="read test", scopes=list(scopes)
    )
    db.session.commit()
    return plaintext


def _credit(user, amount):
    current_app.container.token_service().credit_tokens(
        user.id, amount, TokenTransactionType.PURCHASE
    )
    _db.session.commit()


def _balance(user):
    return current_app.container.token_service().get_balance(user.id)


def _auth_as(monkeypatch, user):
    import vbwd.middleware.auth as auth_mod

    repo = MagicMock()
    repo.find_by_id.return_value = user
    auth_service = MagicMock()
    auth_service.verify_token.return_value = str(user.id)
    monkeypatch.setattr(auth_mod, "UserRepository", lambda *a, **k: repo)
    monkeypatch.setattr(auth_mod, "AuthService", lambda *a, **k: auth_service)


# ----------------------------------------------------------------------
# Public catalogue
# ----------------------------------------------------------------------


def test_public_catalogue_lists_active_only(db, client):
    active = _make_dataset_with_snapshot(db)
    inactive = _make_dataset_with_snapshot(db)
    inactive.is_active = False
    db.session.commit()

    response = client.get("/api/v1/dataset")
    assert response.status_code == 200
    slugs = {item["slug"] for item in response.get_json()["items"]}
    assert active.slug in slugs
    assert inactive.slug not in slugs


# ----------------------------------------------------------------------
# Scoped, metered /data
# ----------------------------------------------------------------------


def test_data_requires_api_key(db, client):
    dataset = _make_dataset_with_snapshot(db)
    response = client.get(f"/api/v1/dataset/{dataset.slug}/data")
    assert response.status_code == 401


def test_entitled_key_gets_data_and_is_metered(db, client):
    user = _make_user(db)
    dataset = _make_dataset_with_snapshot(db, rows_csv=b"city,aqi\nBerlin,42\n")
    _grant(db, user, dataset)
    _credit(user, 10)
    key = _api_key(db, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/data", headers={"X-API-Key": key}
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_data() == b"city,aqi\nBerlin,42\n"
    # One token debited for the metered call (weight 1 for "data").
    assert _balance(user) == 9


def test_unentitled_key_is_forbidden(db, client):
    user = _make_user(db)
    dataset = _make_dataset_with_snapshot(db)
    _credit(user, 10)
    key = _api_key(db, user)  # entitlement deliberately NOT granted

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/data", headers={"X-API-Key": key}
    )
    assert response.status_code == 403
    # No token spent when access is refused.
    assert _balance(user) == 10


def test_over_quota_returns_429(db, client):
    user = _make_user(db)
    dataset = _make_dataset_with_snapshot(db)
    _grant(db, user, dataset)  # entitled but zero token balance
    key = _api_key(db, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/data", headers={"X-API-Key": key}
    )
    assert response.status_code == 429


def test_key_without_scope_is_forbidden(db, client):
    user = _make_user(db)
    dataset = _make_dataset_with_snapshot(db)
    _grant(db, user, dataset)
    _credit(user, 10)
    key = _api_key(db, user, scopes=["other:scope"])

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/data", headers={"X-API-Key": key}
    )
    assert response.status_code == 403


# ----------------------------------------------------------------------
# Preview / meta / download (session auth, entitlement-gated, not metered)
# ----------------------------------------------------------------------


def test_preview_caps_at_100_rows(db, client, monkeypatch):
    user = _make_user(db)
    header = b"idx,value\n"
    body = b"".join(f"{index},{index * 2}\n".encode("utf-8") for index in range(250))
    dataset = _make_dataset_with_snapshot(db, rows_csv=header + body)
    _grant(db, user, dataset)
    _auth_as(monkeypatch, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/preview",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["columns"] == ["idx", "value"]
    assert len(payload["rows"]) == 100
    # Not metered — balance untouched (there is none, and none was created).
    assert _balance(user) == 0


def test_preview_is_entitlement_gated(db, client, monkeypatch):
    user = _make_user(db)
    dataset = _make_dataset_with_snapshot(db)  # no grant
    _auth_as(monkeypatch, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/preview",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 403


def test_meta_returns_issue_metadata(db, client, monkeypatch):
    user = _make_user(db)
    dataset = _make_dataset_with_snapshot(db, rows_csv=b"a,b\n1,2\n")
    _grant(db, user, dataset)
    _auth_as(monkeypatch, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/meta",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 200
    meta = response.get_json()
    assert meta["taken_at"] == "2026-06-01-00-00"
    assert meta["ext"] == "csv"
    assert meta["size_bytes"] == len(b"a,b\n1,2\n")
    assert meta["checksum"]
    assert "location" not in meta  # never expose the raw storage path


def test_download_sets_attachment_header(db, client, monkeypatch):
    user = _make_user(db)
    dataset = _make_dataset_with_snapshot(db, rows_csv=b"a,b\n1,2\n")
    _grant(db, user, dataset)
    _auth_as(monkeypatch, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/download",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 200
    disposition = response.headers.get("Content-Disposition", "")
    assert disposition.startswith("attachment")
    assert dataset.slug in disposition
    assert response.get_data() == b"a,b\n1,2\n"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
