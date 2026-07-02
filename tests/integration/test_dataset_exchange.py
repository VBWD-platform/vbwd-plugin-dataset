"""T10 — Dataset global export/import via the shared data-exchange seam.

A ``DatasetExchanger`` rides the S46 facilities (not a bespoke exporter): the
standard envelope ``{"vbwd_export":"dataset","version":1,"dataset":[rows]}``, the
category FK carried **by slug** (``fk_natural_key_map`` on export + a thin
import-resolver that maps slug→id), and the dataset's snapshot refs carried
nested. Re-import into a clean DB (the dataset dropped, the shared category term
kept) resolves the category by slug and round-trips the row + snapshots + the
``last`` pointer.

Data is seeded through the ORM session (no raw SQL); the shared ``db`` fixture
creates + drops the test DB.
"""
import uuid

from vbwd.services.data_exchange.envelope import ENVELOPE_KEY, build_envelope
from vbwd.services.data_exchange.port import CLUSTER_SALES, ExportSelector

from plugins.cms.src.models.cms_term import CmsTerm
from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.models.dataset_snapshot import DatasetSnapshot
from plugins.dataset.dataset.models.dataset_term import DatasetTerm
from plugins.dataset.dataset.services.data_exchange.dataset_exchangers import (
    build_dataset_exchangers,
)

CATEGORY_TERM_TYPE = "dataset_category"


def _exchanger(session):
    return {
        exchanger.entity_key: exchanger
        for exchanger in build_dataset_exchangers(session)
    }["dataset"]


def _unique(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _seed_category(db, slug):
    term = CmsTerm(term_type=CATEGORY_TERM_TYPE, slug=slug, name="Environment")
    db.session.add(term)
    db.session.commit()
    return term


def _seed_dataset_with_category_and_snapshots(db, category_term):
    dataset = Dataset(
        slug=_unique("air-quality"),
        title="Air Quality",
        description="Hourly air-quality readings",
        source_attribution="Open data portal",
        price=19.0,
    )
    db.session.add(dataset)
    db.session.commit()

    db.session.add(DatasetTerm(dataset_id=dataset.id, term_id=category_term.id))

    older = DatasetSnapshot(
        dataset_id=dataset.id,
        taken_at="2026-05-01-09-00",
        storage_backend="local",
        location="dataset/datasets/environment/air/2026-05-01-09-00.csv",
        ext="csv",
        size_bytes=10,
        checksum="abc",
        ingested_via="upload",
    )
    newest = DatasetSnapshot(
        dataset_id=dataset.id,
        taken_at="2026-06-01-09-00",
        storage_backend="aws",
        location="datasets/environment/air/2026-06-01-09-00.csv",
        ext="csv",
        size_bytes=20,
        checksum="def",
        ingested_via="webhook",
    )
    db.session.add(older)
    db.session.add(newest)
    db.session.commit()

    dataset.last_snapshot_id = newest.id
    db.session.commit()
    return dataset


def test_exchanger_is_registered_on_the_sales_cluster(db):
    exchanger = _exchanger(db.session)
    assert exchanger.entity_key == "dataset"
    assert exchanger.natural_key == "slug"
    assert exchanger.cluster == CLUSTER_SALES


def test_export_carries_category_slug_and_snapshot_refs(db):
    category = _seed_category(db, _unique("environment"))
    dataset = _seed_dataset_with_category_and_snapshots(db, category)

    exchanger = _exchanger(db.session)
    rows = exchanger.export(ExportSelector(ids=[dataset.slug]), include_pii=False).rows

    assert len(rows) == 1
    row = rows[0]
    assert row["slug"] == dataset.slug
    assert row["category_slugs"] == [category.slug]
    # A local UUID must never travel — only the portable ``taken_at`` of ``last``.
    assert "last_snapshot_id" not in row
    assert row["last_snapshot_taken_at"] == "2026-06-01-09-00"
    taken = {snapshot["taken_at"] for snapshot in row["snapshots"]}
    assert taken == {"2026-05-01-09-00", "2026-06-01-09-00"}


def test_round_trip_into_clean_db_resolves_category_by_slug(db):
    category = _seed_category(db, _unique("environment"))
    dataset = _seed_dataset_with_category_and_snapshots(db, category)
    dataset_slug = dataset.slug

    exchanger = _exchanger(db.session)
    rows = exchanger.export(ExportSelector(ids=[dataset_slug]), include_pii=False).rows
    payload = build_envelope("dataset", rows, instance="test")
    assert payload[ENVELOPE_KEY] == "dataset"

    # Clean the dataset (cascade drops its junction + snapshots); the shared
    # category term is kept (categories are imported via the cms term exchanger).
    db.session.query(Dataset).filter(Dataset.slug == dataset_slug).delete()
    db.session.commit()
    assert (
        db.session.query(Dataset).filter(Dataset.slug == dataset_slug).first() is None
    )

    result = exchanger.import_(payload, mode="upsert", dry_run=False)
    assert result.created == 1
    assert not result.errors

    rebuilt = db.session.query(Dataset).filter(Dataset.slug == dataset_slug).first()
    assert rebuilt is not None
    assert rebuilt.title == "Air Quality"

    # Category re-linked by slug (slug→id resolved against the kept term).
    linked_term_ids = [
        str(row[0])
        for row in db.session.query(DatasetTerm.term_id)
        .filter(DatasetTerm.dataset_id == rebuilt.id)
        .all()
    ]
    assert str(category.id) in linked_term_ids

    # Snapshot refs recreated + ``last`` pointer restored by ``taken_at``.
    snapshots = (
        db.session.query(DatasetSnapshot)
        .filter(DatasetSnapshot.dataset_id == rebuilt.id)
        .all()
    )
    assert {snapshot.taken_at for snapshot in snapshots} == {
        "2026-05-01-09-00",
        "2026-06-01-09-00",
    }
    assert rebuilt.last_snapshot_id is not None
    last_snapshot = (
        db.session.query(DatasetSnapshot)
        .filter(DatasetSnapshot.id == rebuilt.last_snapshot_id)
        .first()
    )
    assert last_snapshot.taken_at == "2026-06-01-09-00"


def test_unknown_category_slug_is_error_row_not_a_crash(db):
    slug = _unique("orphan")
    rows = [
        {
            "slug": slug,
            "title": "Orphan",
            "price": 0.0,
            "category_slugs": ["does-not-exist"],
            "snapshots": [],
            "last_snapshot_taken_at": None,
        }
    ]
    payload = build_envelope("dataset", rows, instance="test")

    exchanger = _exchanger(db.session)
    result = exchanger.import_(payload, mode="upsert", dry_run=False)

    # The dataset still imports; the missing category is reported, never crashes.
    assert result.errors
    rebuilt = db.session.query(Dataset).filter(Dataset.slug == slug).first()
    assert rebuilt is not None
    assert (
        db.session.query(DatasetTerm)
        .filter(DatasetTerm.dataset_id == rebuilt.id)
        .count()
        == 0
    )
