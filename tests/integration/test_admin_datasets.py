"""T2 — admin dataset CRUD, list (paged/sorted/filtered), and snapshots.

Exercises the real Flask app + PostgreSQL (rolled-back per test). Auth is
faked in-process by patching the auth middleware collaborators (the established
booking-plugin pattern) so the test stays focused on the dataset routes.
"""
from io import BytesIO
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from vbwd.models.enums import UserRole, UserStatus
from vbwd.models.user import User

HEADERS = {"Authorization": "Bearer valid"}


@pytest.fixture
def client(app):
    return app.test_client()


def _make_admin(db):
    admin = User(
        id=uuid4(),
        email=f"admin-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN,
    )
    db.session.add(admin)
    db.session.commit()
    return admin


def _auth_as_admin(monkeypatch, admin):
    import vbwd.middleware.auth as auth_mod

    repo = MagicMock()
    repo.find_by_id.return_value = admin
    auth_service = MagicMock()
    auth_service.verify_token.return_value = str(admin.id)
    monkeypatch.setattr(auth_mod, "UserRepository", lambda *a, **k: repo)
    monkeypatch.setattr(auth_mod, "AuthService", lambda *a, **k: auth_service)
    monkeypatch.setattr(type(admin), "is_admin", property(lambda self: True))
    monkeypatch.setattr(type(admin), "has_permission", lambda self, perm: True)


def _create_dataset(client, *, title, slug):
    return client.post(
        "/api/v1/admin/datasets",
        json={"title": title, "slug": slug, "price": 100.0},
        headers=HEADERS,
    )


def test_create_read_update_delete_dataset(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    slug = f"air-quality-{uuid4().hex[:8]}"
    created = _create_dataset(client, title="Air Quality", slug=slug)
    assert created.status_code == 201, created.get_json()
    dataset_id = created.get_json()["id"]

    read = client.get(f"/api/v1/admin/datasets/{dataset_id}", headers=HEADERS)
    assert read.status_code == 200
    assert read.get_json()["slug"] == slug

    updated = client.put(
        f"/api/v1/admin/datasets/{dataset_id}",
        json={"title": "Air Quality v2"},
        headers=HEADERS,
    )
    assert updated.status_code == 200
    assert updated.get_json()["title"] == "Air Quality v2"

    deleted = client.delete(f"/api/v1/admin/datasets/{dataset_id}", headers=HEADERS)
    assert deleted.status_code == 200


def test_create_rejects_duplicate_slug(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    slug = f"dup-{uuid4().hex[:8]}"
    assert _create_dataset(client, title="One", slug=slug).status_code == 201
    conflict = _create_dataset(client, title="Two", slug=slug)
    assert conflict.status_code == 409


def test_list_is_paged_sorted_and_searchable(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    marker = uuid4().hex[:8]
    for index in range(3):
        resp = _create_dataset(
            client,
            title=f"Zeta {marker} {index}",
            slug=f"zeta-{marker}-{index}",
        )
        assert resp.status_code == 201

    # Search narrows to our three rows; paginate to 2 per page.
    page_one = client.get(
        f"/api/v1/admin/datasets?search={marker}&per_page=2&page=1"
        "&sort_by=slug&sort_dir=asc",
        headers=HEADERS,
    )
    assert page_one.status_code == 200
    body = page_one.get_json()
    assert body["total"] == 3
    assert body["per_page"] == 2
    assert len(body["items"]) == 2
    slugs = [item["slug"] for item in body["items"]]
    assert slugs == sorted(slugs)  # ascending by slug

    page_two = client.get(
        f"/api/v1/admin/datasets?search={marker}&per_page=2&page=2",
        headers=HEADERS,
    )
    assert len(page_two.get_json()["items"]) == 1


def test_category_filter_returns_no_rows_until_taxonomy_lands(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    _create_dataset(client, title="Filtered", slug=f"filtered-{uuid4().hex[:8]}")
    filtered = client.get(
        "/api/v1/admin/datasets?category=nonexistent", headers=HEADERS
    )
    assert filtered.status_code == 200
    assert filtered.get_json()["items"] == []


def test_upload_snapshot_advances_last_and_lists(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    slug = f"snap-{uuid4().hex[:8]}"
    dataset_id = _create_dataset(client, title="Snap", slug=slug).get_json()["id"]

    upload = client.post(
        f"/api/v1/admin/datasets/{dataset_id}/snapshots",
        data={
            "file": (BytesIO(b"col-a,col-b\n1,2\n"), "may.csv"),
            "taken_at": "2026-05-01-00-00",
            "category": "environment",
        },
        content_type="multipart/form-data",
        headers=HEADERS,
    )
    assert upload.status_code == 201, upload.get_json()
    snapshot = upload.get_json()
    assert snapshot["storage_backend"] == "local"
    assert snapshot["taken_at"] == "2026-05-01-00-00"

    # The dataset now points ``last`` at the uploaded snapshot.
    dataset = client.get(
        f"/api/v1/admin/datasets/{dataset_id}", headers=HEADERS
    ).get_json()
    assert dataset["last_snapshot_id"] == snapshot["id"]

    listing = client.get(
        f"/api/v1/admin/datasets/{dataset_id}/snapshots", headers=HEADERS
    )
    assert listing.status_code == 200
    assert any(item["id"] == snapshot["id"] for item in listing.get_json()["items"])


def _upload_snapshot(client, dataset_id, *, content="col-a,col-b\n1,2\n", taken_at):
    return client.post(
        f"/api/v1/admin/datasets/{dataset_id}/snapshots",
        json={"content": content, "ext": "csv", "taken_at": taken_at},
        headers=HEADERS,
    )


def test_admin_download_snapshot_returns_bytes_with_attachment_header(
    db, client, monkeypatch
):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    slug = f"dl-{uuid4().hex[:8]}"
    dataset_id = _create_dataset(client, title="Downloadable", slug=slug).get_json()[
        "id"
    ]
    payload = "city,aqi\nBerlin,42\n"
    snapshot_id = _upload_snapshot(
        client, dataset_id, content=payload, taken_at="2026-05-01-00-00"
    ).get_json()["id"]

    response = client.get(
        f"/api/v1/admin/datasets/{dataset_id}/snapshots/{snapshot_id}/download",
        headers=HEADERS,
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_data() == payload.encode("utf-8")
    disposition = response.headers.get("Content-Disposition", "")
    assert disposition.startswith("attachment")
    assert slug in disposition


def test_admin_download_unknown_snapshot_returns_404(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    slug = f"dl404-{uuid4().hex[:8]}"
    dataset_id = _create_dataset(client, title="NoSnap", slug=slug).get_json()["id"]

    response = client.get(
        f"/api/v1/admin/datasets/{dataset_id}/snapshots/{uuid4()}/download",
        headers=HEADERS,
    )
    assert response.status_code == 404


def test_admin_download_unavailable_aws_backend_returns_503(db, client, monkeypatch):
    from plugins.dataset.dataset.models.dataset_snapshot import DatasetSnapshot

    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    slug = f"dl503-{uuid4().hex[:8]}"
    dataset_id = _create_dataset(client, title="AwsSnap", slug=slug).get_json()["id"]
    snapshot_id = _upload_snapshot(
        client, dataset_id, taken_at="2026-05-01-00-00"
    ).get_json()["id"]

    # Flip the snapshot to the (unconfigured) AWS backend so the resolver cannot
    # serve it — the download route must degrade to 503, not crash.
    snapshot = db.session.query(DatasetSnapshot).filter_by(id=snapshot_id).first()
    snapshot.storage_backend = "aws"
    db.session.commit()

    response = client.get(
        f"/api/v1/admin/datasets/{dataset_id}/snapshots/{snapshot_id}/download",
        headers=HEADERS,
    )
    assert response.status_code == 503


def test_set_last_snapshot_repoints(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    slug = f"setlast-{uuid4().hex[:8]}"
    dataset_id = _create_dataset(client, title="SetLast", slug=slug).get_json()["id"]

    first = client.post(
        f"/api/v1/admin/datasets/{dataset_id}/snapshots",
        json={"content": "a\n1\n", "ext": "csv", "taken_at": "2026-04-01-00-00"},
        headers=HEADERS,
    ).get_json()
    second = client.post(
        f"/api/v1/admin/datasets/{dataset_id}/snapshots",
        json={"content": "a\n2\n", "ext": "csv", "taken_at": "2026-05-01-00-00"},
        headers=HEADERS,
    ).get_json()

    # After the second upload ``last`` is the second snapshot; repoint to first.
    repoint = client.post(
        f"/api/v1/admin/datasets/{dataset_id}/snapshots/{first['id']}/set-last",
        headers=HEADERS,
    )
    assert repoint.status_code == 200
    assert repoint.get_json()["last_snapshot_id"] == first["id"]
    assert second["id"] != first["id"]
