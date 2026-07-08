"""Test fixtures for the dataset plugin (S110)."""
import os
import sys

import pytest

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
)

os.environ["FLASK_ENV"] = "testing"
os.environ["TESTING"] = "true"
os.environ["TEST_DATA_SEED"] = "true"


def _test_db_url() -> str:
    base = os.getenv("DATABASE_URL", "postgresql://vbwd:vbwd@postgres:5432/vbwd")
    prefix, _, dbname = base.rpartition("/")
    dbname = dbname.split("?")[0]
    return f"{prefix}/{dbname}_test"


def _ensure_test_db(url: str) -> None:
    from sqlalchemy import create_engine, text

    main_url = url.rsplit("/", 1)[0] + "/postgres"
    dbname = url.rsplit("/", 1)[1].split("?")[0]
    engine = create_engine(main_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": dbname}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        engine.dispose()


def _import_schema_models() -> None:
    """Import the dataset models so ``create_all`` emits their tables.

    Load-bearing for the integration schema build: SQLAlchemy only emits DDL for
    mapped classes that have been imported. cms is an optional peer here.
    """
    # cms first: the dataset_term junction FKs cms_term, so that table must be
    # mapped before create_all resolves the foreign key.
    try:
        import plugins.cms.src.models  # noqa: F401
    except ImportError:
        pass

    # subscription next: dataset_plan FKs subscription_tarif_plan, so that table
    # must be mapped before create_all resolves the foreign key (declared dep).
    try:
        import plugins.subscription.subscription.models  # noqa: F401
    except ImportError:
        pass

    import plugins.dataset.dataset.models.dataset  # noqa: F401
    import plugins.dataset.dataset.models.dataset_snapshot  # noqa: F401
    import plugins.dataset.dataset.models.dataset_snapshot_file  # noqa: F401
    import plugins.dataset.dataset.models.dataset_term  # noqa: F401
    import plugins.dataset.dataset.models.dataset_plan  # noqa: F401
    import plugins.dataset.dataset.models.dataset_membership  # noqa: F401
    import plugins.dataset.dataset.models.dataset_access_log  # noqa: F401


def _ensure_dataset_enabled(flask_app) -> None:
    """Enable dataset (+ its declared deps) so ``on_enable`` runs.

    A fresh per-plugin CI clone has no ``plugins.json``, so the plugin is
    discovered-but-not-enabled and its registrations (the ``dataset`` entity
    type, the ``dataset_category`` term type) never fire. Idempotent — a no-op
    when the plugin is already enabled (local dev via the shared manifest).
    """
    from vbwd.plugins.base import PluginStatus

    manager = getattr(flask_app, "plugin_manager", None)
    if manager is None:
        return
    with flask_app.app_context():
        for name in ("subscription", "cms", "dataset"):  # dependencies first
            plugin = manager.get_plugin(name)
            if plugin is None or plugin.status == PluginStatus.ENABLED:
                continue
            try:
                manager.enable_plugin(name)
            except ValueError:
                if plugin.status == PluginStatus.INITIALIZED:
                    plugin.enable()


def _seed_default_currency(db) -> None:
    """Seed the baseline EUR currency so the ``PriceFactory`` resolves a code."""
    from decimal import Decimal
    from uuid import uuid4

    from vbwd.models.currency import Currency

    if not db.session.query(Currency).filter_by(code="EUR").first():
        db.session.add(
            Currency(
                id=uuid4(),
                code="EUR",
                name="Euro",
                symbol="€",
                exchange_rate=Decimal("1.0"),
                decimal_places=2,
            )
        )
        db.session.commit()


@pytest.fixture(scope="session")
def app():
    from vbwd.app import create_app

    url = _test_db_url()
    _ensure_test_db(url)
    test_config = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": url,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "RATELIMIT_ENABLED": True,
        "RATELIMIT_STORAGE_URL": "memory://",
    }
    app = create_app(test_config)
    from vbwd.extensions import limiter

    limiter.reset()

    with app.app_context():
        from vbwd.extensions import db as _db
        from vbwd.testing.integration_db import ensure_schema_and_baseline

        _import_schema_models()
        ensure_schema_and_baseline(_db)

    _ensure_dataset_enabled(app)

    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    """Isolate each test in a rolled-back transaction (self-cleaning, no wipe)."""
    from vbwd.extensions import db

    with app.app_context():
        from vbwd.testing.integration_db import rollback_isolation

        with rollback_isolation(db):
            from vbwd.testing.test_data_seeder import TestDataSeeder

            seeder = TestDataSeeder(db.session)
            seeder.seed()
            _seed_default_currency(db)
            yield db
