"""Vendor-mode for the dataset vertical (S113 parity with shop).

A dataset may be owned by a vendor (``vendor_id`` = their ``vbwd_user`` id). When
``marketplace_enabled`` is on, a user holding ``marketplace.vendor`` can create /
list / read / edit / delete ONLY their own datasets, the one-time order line
carries the selling vendor's id under ``extra_data['vendor_id']``, and the
marketplace vendor-listings provider returns the vendor's dataset dicts. When
off the vendor surface is invisible (403) and no vendor id is stamped.
"""
from uuid import uuid4

import pytest

from plugins.dataset.dataset import routes as dataset_routes
from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.repositories.dataset_repository import DatasetRepository


VENDOR_DATASETS_PATH = "/api/v1/dataset/vendor/datasets"


@pytest.fixture
def client(app):
    return app.test_client()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register(app, email):
    from vbwd.extensions import db
    from vbwd.repositories.user_repository import UserRepository

    user_repository = UserRepository(db.session)
    auth_service = app.container.auth_service()
    if user_repository.find_by_email(email) is None:
        auth_service.register(email=email, password="Vendor123@")
        db.session.commit()
    user = user_repository.find_by_email(email)
    login = auth_service.login(email=email, password="Vendor123@")
    return user, login.token


def _grant_vendor_permission(db, user):
    """Attach a user access level carrying ``marketplace.vendor`` to ``user``."""
    from vbwd.models.role import Permission
    from vbwd.models.user_access_level import UserAccessLevel

    permission = (
        db.session.query(Permission).filter_by(name="marketplace.vendor").first()
    )
    if permission is None:
        permission = Permission(
            id=uuid4(),
            name="marketplace.vendor",
            description="Sell as a vendor",
            resource="marketplace",
            action="vendor",
        )
        db.session.add(permission)
    suffix = uuid4().hex[:8]
    level = UserAccessLevel(
        id=uuid4(),
        slug=f"vendor-{suffix}",
        name=f"Vendor {suffix}",
    )
    level.permissions.append(permission)
    user.assigned_user_access_levels.append(level)
    db.session.commit()


def _make_vendor(app, db, email):
    user, token = _register(app, email)
    _grant_vendor_permission(db, user)
    return user, token


def _enable_marketplace(monkeypatch, enabled):
    monkeypatch.setattr(dataset_routes, "marketplace_enabled", lambda: enabled)


def _dataset_body(title="Vendor Dataset"):
    return {"slug": f"vd-{uuid4().hex[:8]}", "title": title, "price": 12.5}


def _make_user_id(db):
    """Create a real ``vbwd_user`` row so ``dataset.vendor_id`` FK resolves."""
    from vbwd.models.enums import UserRole, UserStatus
    from vbwd.models.user import User

    user = User(
        id=uuid4(),
        email=f"owner-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
    )
    db.session.add(user)
    db.session.flush()
    return user.id


# ── Repository ───────────────────────────────────────────────────────────


def test_find_by_vendor_id_returns_only_that_vendors_datasets(db):
    owner = _make_user_id(db)
    other = _make_user_id(db)
    repo = DatasetRepository(db.session)

    mine = Dataset()
    mine.slug = f"mine-{uuid4().hex[:8]}"
    mine.title = "Mine"
    mine.vendor_id = owner
    repo.save(mine)

    theirs = Dataset()
    theirs.slug = f"theirs-{uuid4().hex[:8]}"
    theirs.title = "Theirs"
    theirs.vendor_id = other
    repo.save(theirs)
    db.session.flush()

    results = repo.find_by_vendor_id(owner)
    assert [dataset.slug for dataset in results] == [mine.slug]


# ── Vendor self-service routes ───────────────────────────────────────────


def test_vendor_create_blocked_when_marketplace_disabled(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"ds-off-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, False)

    resp = client.post(VENDOR_DATASETS_PATH, json=_dataset_body(), headers=_auth(token))
    assert resp.status_code == 403, resp.get_json()


def test_vendor_create_requires_permission(app, db, client, monkeypatch):
    _user, token = _register(app, f"ds-plain-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    resp = client.post(VENDOR_DATASETS_PATH, json=_dataset_body(), headers=_auth(token))
    assert resp.status_code == 403, resp.get_json()


def test_vendor_create_stamps_vendor_id(app, db, client, monkeypatch):
    user, token = _make_vendor(app, db, f"ds-create-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    resp = client.post(
        VENDOR_DATASETS_PATH, json=_dataset_body("My Data"), headers=_auth(token)
    )
    assert resp.status_code == 201, resp.get_json()
    dataset = resp.get_json()["dataset"]
    assert dataset["vendor_id"] == str(user.id)


def test_vendor_create_slug_conflict_returns_409(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"ds-dup-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    body = _dataset_body()

    first = client.post(VENDOR_DATASETS_PATH, json=body, headers=_auth(token))
    assert first.status_code == 201, first.get_json()
    dup = client.post(VENDOR_DATASETS_PATH, json=body, headers=_auth(token))
    assert dup.status_code == 409, dup.get_json()


def test_vendor_lists_only_own_datasets(app, db, client, monkeypatch):
    user, token = _make_vendor(app, db, f"ds-list-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(
        app, db, f"ds-list2-{uuid4().hex[:6]}@example.com"
    )
    _enable_marketplace(monkeypatch, True)

    mine = client.post(
        VENDOR_DATASETS_PATH, json=_dataset_body("Mine"), headers=_auth(token)
    ).get_json()["dataset"]
    client.post(
        VENDOR_DATASETS_PATH, json=_dataset_body("Theirs"), headers=_auth(other_token)
    )

    listed = client.get(VENDOR_DATASETS_PATH, headers=_auth(token))
    assert listed.status_code == 200, listed.get_json()
    slugs = {row["slug"] for row in listed.get_json()["datasets"]}
    assert slugs == {mine["slug"]}


def test_vendor_can_edit_own_dataset(app, db, client, monkeypatch):
    user, token = _make_vendor(app, db, f"ds-edit-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    created = client.post(
        VENDOR_DATASETS_PATH, json=_dataset_body(), headers=_auth(token)
    ).get_json()["dataset"]

    resp = client.put(
        f"{VENDOR_DATASETS_PATH}/{created['id']}",
        json={"title": "Renamed", "price": 20.0},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["dataset"]["title"] == "Renamed"
    assert resp.get_json()["dataset"]["vendor_id"] == str(user.id)


def test_vendor_cannot_read_another_vendors_dataset(app, db, client, monkeypatch):
    _owner, owner_token = _make_vendor(app, db, f"ds-a-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(app, db, f"ds-b-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    created = client.post(
        VENDOR_DATASETS_PATH, json=_dataset_body(), headers=_auth(owner_token)
    ).get_json()["dataset"]

    got = client.get(
        f"{VENDOR_DATASETS_PATH}/{created['id']}", headers=_auth(other_token)
    )
    assert got.status_code == 403, got.get_json()


def test_vendor_cannot_edit_another_vendors_dataset(app, db, client, monkeypatch):
    _owner, owner_token = _make_vendor(app, db, f"ds-c-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(app, db, f"ds-d-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    created = client.post(
        VENDOR_DATASETS_PATH, json=_dataset_body(), headers=_auth(owner_token)
    ).get_json()["dataset"]

    resp = client.put(
        f"{VENDOR_DATASETS_PATH}/{created['id']}",
        json={"title": "Hijacked"},
        headers=_auth(other_token),
    )
    assert resp.status_code == 403, resp.get_json()


def test_vendor_can_delete_own_dataset(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"ds-del-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    created = client.post(
        VENDOR_DATASETS_PATH, json=_dataset_body(), headers=_auth(token)
    ).get_json()["dataset"]

    resp = client.delete(
        f"{VENDOR_DATASETS_PATH}/{created['id']}", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.get_json()
    gone = client.get(f"{VENDOR_DATASETS_PATH}/{created['id']}", headers=_auth(token))
    assert gone.status_code == 404, gone.get_json()


def test_vendor_cannot_delete_another_vendors_dataset(app, db, client, monkeypatch):
    _owner, owner_token = _make_vendor(app, db, f"ds-e-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(app, db, f"ds-f-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    created = client.post(
        VENDOR_DATASETS_PATH, json=_dataset_body(), headers=_auth(owner_token)
    ).get_json()["dataset"]

    resp = client.delete(
        f"{VENDOR_DATASETS_PATH}/{created['id']}", headers=_auth(other_token)
    )
    assert resp.status_code == 403, resp.get_json()


# ── Marketplace vendor-listings provider ─────────────────────────────────


def test_vendor_listings_provider_returns_vendor_dataset_dicts(db):
    from plugins.dataset.dataset.marketplace_listings import vendor_listings_provider

    owner = _make_user_id(db)
    repo = DatasetRepository(db.session)
    mine = Dataset()
    mine.slug = f"listing-{uuid4().hex[:8]}"
    mine.title = "Listing Data"
    mine.vendor_id = owner
    repo.save(mine)
    db.session.flush()

    listings = vendor_listings_provider(owner)
    assert len(listings) == 1
    row = listings[0]
    assert row["slug"] == mine.slug
    assert row["vendor_id"] == str(owner)
    assert "created_at" in row and "updated_at" in row
