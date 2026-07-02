"""T14 — end-to-end dataset seed (idempotent) via ``populate_db``.

Proves the seeder wires one dataset all the way through: the Air-Quality
catalogue row, its ``dataset_category`` term link, two real CSV snapshots (newest
= ``last``), a tax, and a ``DatasetPlan`` grant link — plus the ``data-store`` /
``dataset-detail`` CMS pages and their Vue-component widget records. Everything
goes through ``DatasetService``/``DatasetTaxonomyService`` (DRY) so ``last`` and
the domain events stay consistent, and re-running is a no-op.
"""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from vbwd.models.enums import UserRole, UserStatus
from vbwd.models.user import User

from plugins.cms.src.models.cms_post import CmsPost
from plugins.cms.src.models.cms_widget import CmsWidget
from plugins.dataset import build_dataset_access_service, populate_db
from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.models.dataset_plan import DatasetPlan
from plugins.dataset.dataset.models.dataset_snapshot import DatasetSnapshot

DEMO_SLUG = "air-quality"


@pytest.fixture
def client(app):
    return app.test_client()


def _auth_as(monkeypatch, user):
    import vbwd.middleware.auth as auth_mod

    repo = MagicMock()
    repo.find_by_id.return_value = user
    auth_service = MagicMock()
    auth_service.verify_token.return_value = str(user.id)
    monkeypatch.setattr(auth_mod, "UserRepository", lambda *a, **k: repo)
    monkeypatch.setattr(auth_mod, "AuthService", lambda *a, **k: auth_service)


def test_seed_is_idempotent_and_creates_full_dataset(db, app):
    populate_db.populate(app)
    populate_db.populate(app)  # a second run must not duplicate anything

    datasets = db.session.query(Dataset).filter_by(slug=DEMO_SLUG).all()
    assert len(datasets) == 1
    dataset = datasets[0]
    assert dataset.raw_price > 0
    assert dataset.taxes, "the seeded dataset should carry a real tax"

    snapshots = db.session.query(DatasetSnapshot).filter_by(dataset_id=dataset.id).all()
    assert len(snapshots) == 2
    newest = max(snapshots, key=lambda snapshot: snapshot.taken_at)
    assert str(dataset.last_snapshot_id) == str(newest.id)

    plan_links = db.session.query(DatasetPlan).filter_by(dataset_id=dataset.id).all()
    assert len(plan_links) == 1


def test_seed_links_the_dataset_category(db, app):
    from plugins.dataset.dataset.models.dataset_term import DatasetTerm

    populate_db.populate(app)

    dataset = db.session.query(Dataset).filter_by(slug=DEMO_SLUG).first()
    links = db.session.query(DatasetTerm).filter_by(dataset_id=dataset.id).all()
    assert links, "the seeded dataset should be linked to its category term"


def test_seed_creates_cms_data_store_page_and_widget(db, app):
    populate_db.populate(app)

    catalogue_page = db.session.query(CmsPost).filter_by(slug="data-store").first()
    assert catalogue_page is not None
    detail_page = db.session.query(CmsPost).filter_by(slug="dataset-detail").first()
    assert detail_page is not None

    catalogue_widget = (
        db.session.query(CmsWidget).filter_by(slug="dataset-catalogue").first()
    )
    assert catalogue_widget is not None
    assert catalogue_widget.content_json.get("component") == "DatasetCatalogue"

    detail_widget = (
        db.session.query(CmsWidget).filter_by(slug="dataset-detail-widget").first()
    )
    assert detail_widget is not None
    assert detail_widget.content_json.get("component") == "DatasetDetail"


def test_seeded_dataset_is_listed_and_previewable(db, app, client, monkeypatch):
    populate_db.populate(app)

    listing = client.get("/api/v1/dataset")
    assert listing.status_code == 200
    slugs = {item["slug"] for item in listing.get_json()["items"]}
    assert DEMO_SLUG in slugs

    dataset = db.session.query(Dataset).filter_by(slug=DEMO_SLUG).first()
    user = User(
        id=uuid4(),
        email=f"buyer-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
    )
    db.session.add(user)
    db.session.commit()
    build_dataset_access_service().grant(user.id, dataset.id, triggered_by="test")
    db.session.commit()
    _auth_as(monkeypatch, user)

    preview = client.get(
        f"/api/v1/dataset/{DEMO_SLUG}/preview",
        headers={"Authorization": "Bearer valid"},
    )
    assert preview.status_code == 200, preview.get_json()
    payload = preview.get_json()
    assert payload["columns"]
    assert len(payload["rows"]) >= 1


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
