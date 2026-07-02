"""Dataset plugin demo data — envelope-aware, idempotent seeder (S110 T10/T14).

Seeds ONE dataset end-to-end so the vertical renders and sells out of the box:

* the **Air-Quality** catalogue row (the best recurring SKU) is imported through
  the shared ``DatasetExchanger`` (DRY: the same path Settings → Import/Export
  uses), with its ``dataset_category`` term seeded first so the FK resolves by
  slug on import;
* a real tax link (``VAT_DE``) so ``PriceFactory`` yields a brutto price;
* **two** timestamped CSV snapshots written to the local storage backend through
  ``DatasetService`` (so ``last``/events stay consistent), newest set as ``last``;
* a ``DatasetPlan`` link to a recurring tariff plan that grants access;
* the ``data-store`` / ``dataset-detail`` CMS pages plus the ``DatasetCatalogue``
  / ``DatasetDetail`` Vue-component widget records (a widget needs a seeded
  record, not just fe registration) so the Data store catalogue renders.

Idempotent: every step upserts by slug / skips when already present, so running
it twice is a no-op.
"""
import logging

logger = logging.getLogger(__name__)

# The demo dataset + its category (a shared cms term of type ``dataset_category``).
DEMO_DATASET_SLUG = "air-quality"
DEMO_CATEGORY = {"slug": "environment", "name": "Environment"}

# The recurring plan that grants access to the demo dataset (copied ghrm pattern:
# plan → grant link via ``DatasetPlan``). Reused by slug if it already exists.
DATASET_PLAN_SLUG = "dataset-air-quality"
DATASET_PLAN_NAME = "Air Quality Data Access"

# The standard German VAT the demo dataset is taxed at (seeded by the core tax
# seeder; created here if absent so the seed is self-contained).
DEMO_TAX_CODE = "VAT_DE"

# Two real CSV snapshots (oldest first so the last one added becomes ``last``).
_SNAPSHOT_HEADER = "station_id,city,pollutant,value,unit,measured_at\n"
DEMO_SNAPSHOTS = (
    (
        "2026-04-01-00-00",
        _SNAPSHOT_HEADER
        + "DE-BE-001,Berlin,PM2.5,12.4,ug/m3,2026-04-01T00:00:00Z\n"
        + "DE-MU-001,Munich,NO2,18.7,ug/m3,2026-04-01T00:00:00Z\n"
        + "DE-HH-001,Hamburg,O3,55.3,ug/m3,2026-04-01T00:00:00Z\n"
        + "DE-CO-001,Cologne,PM2.5,9.8,ug/m3,2026-04-01T00:00:00Z\n",
    ),
    (
        "2026-05-01-00-00",
        _SNAPSHOT_HEADER
        + "DE-BE-001,Berlin,PM2.5,10.1,ug/m3,2026-05-01T00:00:00Z\n"
        + "DE-MU-001,Munich,NO2,16.2,ug/m3,2026-05-01T00:00:00Z\n"
        + "DE-HH-001,Hamburg,O3,61.0,ug/m3,2026-05-01T00:00:00Z\n"
        + "DE-CO-001,Cologne,PM2.5,8.4,ug/m3,2026-05-01T00:00:00Z\n",
    ),
)

# One demo dataset in the standard data-exchange envelope. Snapshots are seeded
# separately (through the service) so ``last`` and the domain events stay DRY;
# the envelope keeps the catalogue row + its category link so the exporter /
# importer round-trips.
DEMO_DATASET_ENVELOPE = {
    "vbwd_export": "dataset",
    "version": 1,
    "dataset": [
        {
            "slug": DEMO_DATASET_SLUG,
            "title": "Air Quality",
            "description": "Hourly air-quality readings for live European cities.",
            "source_attribution": "Open-data air-quality portal",
            "price": 19.0,
            "price_display_mode": None,
            "is_active": True,
            "category_slugs": [DEMO_CATEGORY["slug"]],
            "snapshots": [],
            "last_snapshot_taken_at": None,
        },
    ],
}

# CMS wiring for the Data store (mirrors ghrm's category page/widget seed).
CATALOGUE_LAYOUT_SLUG = "dataset-catalogue-layout"
DETAIL_LAYOUT_SLUG = "dataset-detail-layout"
CATALOGUE_WIDGET_SLUG = "dataset-catalogue"
DETAIL_WIDGET_SLUG = "dataset-detail-widget"
CATALOGUE_PAGE_SLUG = "data-store"
DETAIL_PAGE_SLUG = "dataset-detail"


def populate(app=None):
    """Populate dataset demo data (idempotent)."""
    from vbwd.extensions import db

    _ensure_demo_category(db.session, DEMO_CATEGORY)
    _import_dataset_envelope(db.session, DEMO_DATASET_ENVELOPE)
    db.session.commit()

    _seed_dataset_tax(db.session)
    _seed_snapshots()
    _seed_dataset_plan(db.session)
    _seed_cms_data_store(db.session)
    db.session.commit()


def _ensure_demo_category(session, category):
    """Seed the ``dataset_category`` term the demo dataset links to (idempotent)."""
    from plugins.cms.src.models.cms_term import CmsTerm

    from plugins.dataset import DATASET_CATEGORY_TERM_TYPE

    existing = (
        session.query(CmsTerm)
        .filter(
            CmsTerm.term_type == DATASET_CATEGORY_TERM_TYPE,
            CmsTerm.slug == category["slug"],
        )
        .first()
    )
    if existing is None:
        session.add(
            CmsTerm(
                term_type=DATASET_CATEGORY_TERM_TYPE,
                slug=category["slug"],
                name=category["name"],
            )
        )
        session.flush()


def _import_dataset_envelope(session, envelope):
    """Import the demo envelope through the shared dataset exchanger (DRY).

    The importer pops keys off each row in place, so a deep copy is passed to
    keep the module-level ``DEMO_DATASET_ENVELOPE`` intact across re-runs (a
    stripped envelope would silently drop the category link on the second call).
    """
    import copy

    from plugins.dataset.dataset.services.data_exchange.dataset_exchangers import (
        build_dataset_exchangers,
    )

    exchanger = build_dataset_exchangers(session)[0]
    exchanger.import_(copy.deepcopy(envelope), mode="upsert", dry_run=False)


def _demo_dataset(session):
    """Resolve the seeded demo dataset row (``None`` if the import skipped it)."""
    from plugins.dataset.dataset.repositories.dataset_repository import (
        DatasetRepository,
    )

    return DatasetRepository(session).find_by_slug(DEMO_DATASET_SLUG)


def _seed_dataset_tax(session):
    """Link the demo dataset to the standard VAT tax (idempotent)."""
    from vbwd.models.tax import Tax

    dataset = _demo_dataset(session)
    if dataset is None:
        return

    tax = session.query(Tax).filter_by(code=DEMO_TAX_CODE).first()
    if tax is None:
        from decimal import Decimal

        tax = Tax(
            name="German VAT",
            code=DEMO_TAX_CODE,
            rate=Decimal("19.00"),
            country_code="DE",
            tax_class="standard",
            is_active=True,
        )
        session.add(tax)
        session.flush()

    if tax not in dataset.taxes:
        dataset.taxes.append(tax)
        session.flush()


def _seed_snapshots():
    """Write the two demo CSV snapshots through ``DatasetService`` (idempotent).

    Goes through the service (not the repository) so ``last`` advances and the
    ``dataset.updated`` event fires exactly as in production (DRY). Skips when the
    dataset already has snapshots, so a re-run does not duplicate them.
    """
    from flask import current_app

    from vbwd.events.bus import event_bus
    from vbwd.extensions import db

    from plugins.dataset.dataset.repositories.dataset_repository import (
        DatasetRepository,
    )
    from plugins.dataset.dataset.repositories.dataset_snapshot_repository import (
        DatasetSnapshotRepository,
    )
    from plugins.dataset.dataset.services.dataset_service import DatasetService
    from plugins.dataset.dataset.services.storage.local_backend import (
        LocalArchiveBackend,
    )

    dataset_repository = DatasetRepository(db.session)
    dataset = dataset_repository.find_by_slug(DEMO_DATASET_SLUG)
    if dataset is None:
        return

    snapshot_repository = DatasetSnapshotRepository(db.session)
    if snapshot_repository.find_for_dataset(str(dataset.id)):
        return  # already seeded — keep re-runs a no-op

    service = DatasetService(
        dataset_repository=dataset_repository,
        snapshot_repository=snapshot_repository,
        storage_backend=LocalArchiveBackend(current_app.container.filesystem_manager()),
        event_bus=event_bus,
    )
    for taken_at, csv_text in DEMO_SNAPSHOTS:
        service.add_snapshot(
            str(dataset.id),
            data=csv_text.encode("utf-8"),
            ext="csv",
            taken_at=taken_at,
            category_slug=DEMO_CATEGORY["slug"],
        )


def _seed_dataset_plan(session):
    """Link the demo dataset to a recurring tariff plan that grants access."""
    from plugins.dataset.dataset.models.dataset_plan import DatasetPlan

    dataset = _demo_dataset(session)
    if dataset is None:
        return

    plan = _get_or_create_dataset_plan(session)
    existing = (
        session.query(DatasetPlan)
        .filter_by(dataset_id=dataset.id, tariff_plan_id=plan.id)
        .first()
    )
    if existing is None:
        session.add(DatasetPlan(dataset_id=dataset.id, tariff_plan_id=plan.id))
        session.flush()


def _get_or_create_dataset_plan(session):
    """Return the demo access plan by slug, creating a monthly one if absent."""
    from decimal import Decimal

    from vbwd.models.currency import Currency
    from vbwd.models.enums import BillingPeriod

    from plugins.subscription.subscription.models.tarif_plan import TarifPlan

    plan = session.query(TarifPlan).filter_by(slug=DATASET_PLAN_SLUG).first()
    if plan:
        return plan

    if not session.query(Currency).filter_by(code="EUR").first():
        session.add(
            Currency(code="EUR", name="Euro", symbol="€", exchange_rate=Decimal("1.0"))
        )
        session.flush()

    plan = TarifPlan(
        name=DATASET_PLAN_NAME,
        slug=DATASET_PLAN_SLUG,
        description="Recurring access to the Air Quality dataset.",
        price=19.0,
        billing_period=BillingPeriod.MONTHLY,
        trial_days=0,
        features={},
        is_active=True,
        sort_order=0,
    )
    session.add(plan)
    session.flush()
    return plan


def _get_or_create(session, model, slug, **kwargs):
    """Return an existing row by slug, or create and flush a new one."""
    row = session.query(model).filter_by(slug=slug).first()
    if row:
        return row
    row = model(slug=slug, **kwargs)
    session.add(row)
    session.flush()
    return row


def _seed_cms_data_store(session):
    """Seed the Data store CMS pages + Vue-component widget records (idempotent).

    Mirrors ghrm's category page/widget seed: a layout with a ``vue`` area, a
    ``vue-component`` widget record whose ``component`` matches the fe-user
    ``registerCmsVueComponent`` name, the widget assigned into the layout area,
    and a CmsPost page bound to the layout. A widget renders only from a seeded
    record, so this is required (not just fe registration).
    """
    try:
        from plugins.cms.src.models.cms_layout import CmsLayout
        from plugins.cms.src.models.cms_layout_widget import CmsLayoutWidget
        from plugins.cms.src.models.cms_post import CmsPost
        from plugins.cms.src.models.cms_widget import CmsWidget
    except ImportError as cms_import_error:
        logger.warning(
            "[dataset] Data store CMS page not seeded (cms absent?): %s",
            cms_import_error,
        )
        return

    catalogue_layout = _get_or_create(
        session,
        CmsLayout,
        CATALOGUE_LAYOUT_SLUG,
        name="Dataset Catalogue",
        areas=[
            {"name": "header", "type": "header", "label": "Header"},
            {"name": "dataset-catalogue", "type": "vue", "label": "Catalogue"},
            {"name": "footer", "type": "footer", "label": "Footer"},
        ],
        sort_order=10,
        is_active=True,
    )
    detail_layout = _get_or_create(
        session,
        CmsLayout,
        DETAIL_LAYOUT_SLUG,
        name="Dataset Detail",
        areas=[
            {"name": "header", "type": "header", "label": "Header"},
            {"name": "dataset-detail", "type": "vue", "label": "Detail"},
            {"name": "footer", "type": "footer", "label": "Footer"},
        ],
        sort_order=11,
        is_active=True,
    )

    catalogue_widget = _get_or_create(
        session,
        CmsWidget,
        CATALOGUE_WIDGET_SLUG,
        name="Dataset Catalogue",
        widget_type="vue-component",
        content_json={"component": "DatasetCatalogue", "items_per_page": 12},
        is_active=True,
    )
    detail_widget = _get_or_create(
        session,
        CmsWidget,
        DETAIL_WIDGET_SLUG,
        name="Dataset Detail",
        widget_type="vue-component",
        content_json={"component": "DatasetDetail"},
        is_active=True,
    )

    _assign_widget(
        session,
        CmsLayoutWidget,
        catalogue_layout,
        catalogue_widget,
        "dataset-catalogue",
    )
    _assign_widget(
        session, CmsLayoutWidget, detail_layout, detail_widget, "dataset-detail"
    )

    _get_or_create(
        session,
        CmsPost,
        CATALOGUE_PAGE_SLUG,
        type="page",
        title="Data Store",
        language="en",
        content_json={"type": "doc", "content": []},
        status="published",
        sort_order=0,
        layout_id=catalogue_layout.id,
        meta_title="Data Store",
        robots="index,follow",
    )
    _get_or_create(
        session,
        CmsPost,
        DETAIL_PAGE_SLUG,
        type="page",
        title="Dataset Detail",
        language="en",
        content_json={"type": "doc", "content": []},
        status="published",
        sort_order=1,
        layout_id=detail_layout.id,
        meta_title="Dataset Detail",
        robots="noindex",
    )


def _assign_widget(session, layout_widget_model, layout, widget, area_name):
    """Assign a widget into a layout area once (idempotent)."""
    exists = (
        session.query(layout_widget_model)
        .filter_by(layout_id=layout.id, widget_id=widget.id, area_name=area_name)
        .first()
    )
    if exists is None:
        session.add(
            layout_widget_model(
                layout_id=layout.id,
                widget_id=widget.id,
                area_name=area_name,
                sort_order=0,
            )
        )
        session.flush()


if __name__ == "__main__":
    from vbwd.app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        populate(flask_app)
