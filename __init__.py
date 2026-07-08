"""Dataset plugin — sell access to processed public-data products (S110).

A dataset is a ``Priceable`` sellable whose purchase grants an entitlement to a
scoped read API and a user dashboard. The catalogue reuses the shared term
engine (categories + tags) and the plan-grants-access lifecycle is *copied* from
ghrm — this plugin does NOT import or depend on ghrm.

Class MUST be defined in this ``__init__.py`` (not re-exported): plugin
discovery requires ``obj.__module__ == "plugins.dataset"`` (manager.py).
"""
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from vbwd.plugins.base import BasePlugin, PluginMetadata, PublicRouteDeclaration

if TYPE_CHECKING:
    from flask import Blueprint

# Ops-tunable defaults. Access-plan ids, grace period, per-endpoint API weights
# and the optional AWS backend settings live in config / ``var`` (never
# hardcoded deep in code). This dict is the fallback; the host merges the
# on-disk ``config.json`` over it.
DEFAULT_CONFIG: Dict[str, Any] = {
    "debug_mode": False,
    # Vendor-mode (marketplace): when on, users holding ``marketplace.vendor``
    # own the datasets they create and the selling vendor is stamped onto the
    # buyer invoice line at checkout. Off = classic single-owner catalogue.
    "marketplace_enabled": False,
    # Which tariff plan ids grant dataset access (copied from ghrm's
    # plan-grants-access pattern; the per-DatasetPlan link arrives in T7).
    "dataset_access_plan_ids": [],
    # Days after cancellation before access is revoked when a plan/package does
    # not specify its own (copied from ghrm's ``grace_period_fallback_days``).
    "grace_period_fallback_days": 7,
    # Metered read cost per endpoint (net-new weighting lands in S111; the
    # included allowance + weights are declared here so ops owns them).
    "api_included_allowance": 1000,
    "api_endpoint_weights": {
        "data": 1,
        "preview": 0,
        "meta": 0,
    },
    # Optional AWS S3 backend (T3). Off unless configured; a missing secret must
    # degrade gracefully (local backend keeps working).
    "aws": {
        "enabled": False,
        "bucket": "",
        "prefix": "datasets",
        "region": "",
    },
    # Per-source HMAC secret for the protected inbound ingest webhooks (T9). Keyed
    # by source (``pipeline`` / ``aws``); ops sets the real secrets in the plugin
    # config / ``var`` (never hardcoded, never in the image). An empty/absent
    # secret disables that source (its webhook rejects every call with 401).
    "webhook_secrets": {
        "pipeline": "",
        "aws": "",
    },
    # How far (seconds) an inbound webhook timestamp may be from now before it is
    # rejected as stale/replayed (the replay guard window).
    "webhook_timestamp_tolerance_seconds": 300,
    # Companion-file limits for issue bundles (S124). Ops-tunable, never
    # hardcoded at a call site: the max member-file size (50 MiB default) and the
    # allow-list of member extensions an attach may carry.
    "max_file_size_bytes": 52428800,
    "allowed_file_extensions": [
        "csv",
        "tsv",
        "json",
        "xlsx",
        "parquet",
        "pdf",
        "png",
        "jpg",
        "jpeg",
        "svg",
        "txt",
        "md",
        "zip",
    ],
}

# The entity type used for the generic tags / custom-fields framework and the
# term type used for dataset categories (both registered on enable).
DATASET_ENTITY_TYPE = "dataset"
DATASET_CATEGORY_TERM_TYPE = "dataset_category"

# The API-key scope the scoped read API enforces (single source of truth;
# routes.py imports this). Declared as a user-grantable ``api_scope`` below so
# it shows up in the Manage-API-keys UI.
DATASET_READ_SCOPE = "dataset:read"

# The subscription lifecycle events the access service reacts to (copied from
# ghrm's set — but through the core EventBus, with no ghrm dependency).
SUBSCRIPTION_ACTIVATED = "subscription.activated"
SUBSCRIPTION_CANCELLED = "subscription.cancelled"
SUBSCRIPTION_PAYMENT_FAILED = "subscription.payment_failed"
SUBSCRIPTION_RENEWED = "subscription.renewed"
INVOICE_PAID = "invoice.paid"


class _SubscriptionEntitlementsAdapter:
    """Satisfies the dataset-owned ``ISubscriptionEntitlements`` port (DIP).

    Delegates to the subscription plugin's read model. This is the SINGLE place
    the dataset plugin reaches the subscription concrete — legitimate because
    dataset declares ``dependencies=["subscription", "cms"]``. The import is
    local so it is reached only when an entitlement read actually happens. ghrm
    is NOT imported.
    """

    def active_plan_ids(self, user_id):
        from plugins.subscription.subscription.services.subscription_read_model import (
            SubscriptionReadModel,
        )

        return SubscriptionReadModel().active_plan_ids(user_id)


def build_dataset_access_service():
    """Composition root for ``DatasetAccessService`` (fresh ``db.session``).

    The ONLY place the access repos are wired. Shared by the plugin's event
    listeners, the line-item handler, the one-time handler and the grace
    scheduler so access state has one construction home (DRY). Reads the
    grace-period fallback from the plugin config (ops-tunable, never hardcoded).
    """
    from vbwd.extensions import db

    from plugins.dataset.dataset.repositories.dataset_access_log_repository import (
        DatasetAccessLogRepository,
    )
    from plugins.dataset.dataset.repositories.dataset_membership_repository import (
        DatasetMembershipRepository,
    )
    from plugins.dataset.dataset.repositories.dataset_plan_repository import (
        DatasetPlanRepository,
    )
    from plugins.dataset.dataset.services.dataset_access_service import (
        DatasetAccessService,
        DEFAULT_GRACE_PERIOD_DAYS,
    )

    config = _current_plugin_config()
    return DatasetAccessService(
        membership_repository=DatasetMembershipRepository(db.session),
        access_log_repository=DatasetAccessLogRepository(db.session),
        dataset_plan_repository=DatasetPlanRepository(db.session),
        grace_period_fallback_days=config.get(
            "grace_period_fallback_days", DEFAULT_GRACE_PERIOD_DAYS
        ),
    )


def build_dataset_entitlement_service():
    """Composition root for the ``IDatasetEntitlements`` read port (DIP).

    Composes the subscription entitlements adapter with the dataset↔plan links
    so callers depend only on ``active_dataset_ids`` (ISP). Fresh ``db.session``.
    """
    from vbwd.extensions import db

    from plugins.dataset.dataset.repositories.dataset_plan_repository import (
        DatasetPlanRepository,
    )
    from plugins.dataset.dataset.services.dataset_entitlement_service import (
        DatasetEntitlementService,
    )

    return DatasetEntitlementService(
        subscription_entitlements=_SubscriptionEntitlementsAdapter(),
        dataset_plan_repository=DatasetPlanRepository(db.session),
    )


def build_snapshot_file_service():
    """Composition root for ``SnapshotFileService`` (fresh ``db.session``).

    Mirrors the ``build_dataset_*`` factories: wires the member-file repo, the
    snapshot repo (to read the primary for the uniform list / zip), the local
    storage backend (writes use local, the MVP default) and the core EventBus.
    The size/extension limits come from the live plugin config (ops-tunable,
    never hardcoded).
    """
    from flask import current_app

    from vbwd.events.bus import event_bus
    from vbwd.extensions import db

    from plugins.dataset.dataset.repositories.dataset_snapshot_file_repository import (
        DatasetSnapshotFileRepository,
    )
    from plugins.dataset.dataset.repositories.dataset_snapshot_repository import (
        DatasetSnapshotRepository,
    )
    from plugins.dataset.dataset.services.snapshot_file_service import (
        DEFAULT_ALLOWED_FILE_EXTENSIONS,
        DEFAULT_MAX_FILE_SIZE_BYTES,
        SnapshotFileService,
    )
    from plugins.dataset.dataset.services.storage.local_backend import (
        LocalArchiveBackend,
    )

    config = _current_plugin_config()
    return SnapshotFileService(
        snapshot_file_repository=DatasetSnapshotFileRepository(db.session),
        snapshot_repository=DatasetSnapshotRepository(db.session),
        storage_backend=LocalArchiveBackend(current_app.container.filesystem_manager()),
        event_bus=event_bus,
        max_file_size_bytes=config.get(
            "max_file_size_bytes", DEFAULT_MAX_FILE_SIZE_BYTES
        ),
        allowed_file_extensions=config.get(
            "allowed_file_extensions", DEFAULT_ALLOWED_FILE_EXTENSIONS
        ),
    )


def _lookup_invoice_by_number(invoice_number):
    """Resolve a ``UserInvoice`` by its human invoice number (``invoice.paid``).

    The ``invoice.paid`` payload keys the invoice by its number (mirrors how the
    booking one-time handler resolves it). Returns ``None`` when not found.
    """
    from vbwd.extensions import db
    from vbwd.models.invoice import UserInvoice

    return (
        db.session.query(UserInvoice).filter_by(invoice_number=invoice_number).first()
    )


def _current_plugin_config() -> Dict[str, Any]:
    """Best-effort read of the live dataset plugin config (fallback: defaults).

    Ops-tunable values (grace period, access plan ids) live in the plugin config
    / ``var`` and are read here so no default is hardcoded at a call site.
    """
    try:
        from flask import current_app

        config_store = getattr(current_app, "config_store", None)
        if config_store is not None:
            stored = config_store.get_config("dataset")
            if stored:
                return {**DEFAULT_CONFIG, **stored}
    except Exception:  # noqa: BLE001 — config read must never break a handler
        pass
    return {**DEFAULT_CONFIG}


class DatasetPlugin(BasePlugin):
    """Datasets vertical — sellable, versioned data products."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="dataset",
            version="26.6.1",
            author="VBWD Team",
            description=(
                "Datasets — sell access to processed public-data products via "
                "subscription entitlements and a scoped read API"
            ),
            # Taxonomy reuses the cms term engine; access reuses the subscription
            # read model. ghrm is deliberately NOT a dependency (its access
            # pattern is copied, not imported).
            dependencies=["subscription", "cms"],
        )

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> None:
        merged: Dict[str, Any] = {**DEFAULT_CONFIG}
        if config:
            merged.update(config)
        super().initialize(merged)

    def get_blueprint(self) -> Optional["Blueprint"]:
        from plugins.dataset.dataset.routes import dataset_bp

        return dataset_bp

    def get_url_prefix(self) -> Optional[str]:
        # Routes use absolute paths (admin namespace now; public namespace in a
        # later task), so the blueprint carries no prefix.
        return ""

    def declare_public_routes(self) -> PublicRouteDeclaration:
        """The public dataset storefront reads + the HMAC-gated ingest webhook.

        The catalogue reads carry no ``@require_auth`` (a storefront browses
        pre-login); the ingest webhook is HMAC-signature-gated in the handler
        (per-source secret + replay window), so it is public-by-necessity. The
        session-auth ``/preview`` / ``/meta`` / ``/download`` / ``/my`` routes
        and the API-key ``/<slug>/data`` route carry auth and are NOT declared.
        """
        return PublicRouteDeclaration(
            read={
                "/api/v1/dataset": "Public dataset catalogue listing for the storefront.",
                "/api/v1/dataset/<slug>": "Public single dataset detail for the storefront.",
                "/api/v1/dataset/categories": "Public dataset category listing for the storefront.",
            },
            mutation={
                "/api/v1/dataset/webhooks/<source>/ingest": (
                    "Inbound dataset ingest webhook; HMAC signature + replay "
                    "window verified in-handler (per-source secret)."
                ),
            },
        )

    @property
    def admin_permissions(self) -> List[Dict[str, str]]:
        return [
            {"key": "dataset.view", "label": "View datasets", "group": "Datasets"},
            {
                "key": "dataset.manage",
                "label": "Manage datasets",
                "group": "Datasets",
            },
        ]

    @property
    def user_permissions(self) -> List[Dict[str, str]]:
        return [
            {"key": "dataset.view", "label": "View datasets", "group": "Datasets"},
            {
                "key": "dataset.manage",
                "label": "Manage datasets",
                "group": "Datasets",
            },
        ]

    @property
    def api_scopes(self) -> List[Dict[str, Any]]:
        """The API-key scope the scoped dataset read API enforces (read by core S52).

        The endpoints guard on the ``dataset:read`` **scope** (see
        ``DATASET_READ_SCOPE`` in ``routes.py``) — NOT on a user permission.
        ``user_grantable`` lets an entitled user self-mint a ``dataset:read`` key
        at ``/dashboard/api-keys``; without this declaration the scope never
        appears in the key-creation UI, so the access page can never find a key
        carrying it. Granting the scope is harmless on its own — every scoped
        endpoint additionally checks the caller's dataset entitlement.
        """
        return [
            {
                "key": DATASET_READ_SCOPE,
                "label": "Dataset read (API)",
                "description": (
                    "Read a purchased dataset's data and archived snapshots "
                    "through the scoped dataset API."
                ),
                "user_grantable": True,
            }
        ]

    def on_enable(self) -> None:
        """Register the dataset taxonomy + taggable entity type.

        Idempotent: both registries replace by key, so re-enabling is a no-op in
        effect. A missing optional dependency (cms) must not abort enable — the
        term-type registration is guarded so the plugin still enables and the
        core entity type is still registered (Liskov: the disabled/absent-dep
        path never breaks the caller).
        """
        import logging

        from vbwd.services.entity_type_registry import (
            EntityTypeRegistration,
            register_entity_type,
        )

        register_entity_type(
            EntityTypeRegistration(
                DATASET_ENTITY_TYPE,
                "Dataset",
                "dataset.manage",
            )
        )

        try:
            from plugins.cms.src.services.term_type_registry import (
                TermType,
                register_term_type,
            )

            register_term_type(
                TermType(
                    key=DATASET_CATEGORY_TERM_TYPE,
                    label="Dataset category",
                    hierarchical=True,
                )
            )
        except ImportError as term_type_import_error:
            logging.getLogger(__name__).warning(
                "[dataset] Category term type not registered (cms absent?): %s",
                term_type_import_error,
            )

        self._register_data_exchangers()
        self._register_marketplace_listings()

    def _register_marketplace_listings(self) -> None:
        """Contribute this vendor's datasets to the marketplace listings registry.

        The soft import lives HERE (the plugin wiring root, not dataset source)
        so dataset's source stays marketplace-free (test_vendor_mode_contract)
        AND the per-plugin isolated CI (dataset without marketplace) still
        enables cleanly (Liskov: the absent-peer path never breaks enable).
        """
        try:
            from plugins.marketplace.marketplace.services import (
                vendor_listings_registry as marketplace_listings_registry,
            )
        except ImportError:
            return
        from plugins.dataset.dataset.marketplace_listings import (
            DATASET_LISTING_TYPE_ID,
            vendor_listings_provider,
        )

        marketplace_listings_registry.register_vendor_listings_provider(
            DATASET_LISTING_TYPE_ID, vendor_listings_provider
        )

    def _register_data_exchangers(self) -> None:
        """Register the dataset entity exchanger into the shared data-exchange seam.

        Core declares none of these (it stays agnostic); the plugin adds them on
        enable through the shared ``db.session`` so datasets appear on the generic
        Settings → Import/Export page and round-trip via the standard envelope.
        Clear-safe: re-registering replaces by key (per-test app re-enable). A
        failure here must not abort enable (Liskov: the plugin still works).
        """
        import logging

        try:
            from vbwd.extensions import db

            from plugins.dataset.dataset.services.data_exchange.dataset_exchangers import (  # noqa: E501
                register_dataset_exchangers,
            )

            register_dataset_exchangers(db.session)
        except Exception as exchanger_error:  # noqa: BLE001 — never abort enable
            logging.getLogger(__name__).warning(
                "[dataset] Failed to register data exchangers: %s", exchanger_error
            )

    def register_line_item_handlers(self, registry: Any) -> None:
        """Register the recurring dataset line-item handler (T6).

        The handler grants/revokes dataset access on payment
        capture/refund/restore, delegating to ``DatasetAccessService`` (DRY).
        """
        from plugins.dataset.dataset.handlers.line_item_handler import (
            DatasetLineItemHandler,
        )

        registry.register(DatasetLineItemHandler(build_dataset_access_service))

    def register_event_handlers(self, bus: Any) -> None:
        """Subscribe the dataset access lifecycle to the core EventBus (T6/T7).

        * subscription.activated/renewed → grant/restore dataset access,
        * subscription.cancelled/payment_failed → grace-revoke,
        * invoice.paid → grant one-time (line-item-free) dataset orders.

        Copies ghrm's subscription-lifecycle pattern but through the core
        EventBus with NO ghrm dependency. A handler builds its access service
        per call (fresh ``db.session``) via the composition root.
        """

        def on_activated(_name: str, payload: dict) -> None:
            build_dataset_access_service().on_subscription_activated(
                payload.get("user_id"), payload.get("plan_id")
            )

        def on_cancelled(_name: str, payload: dict) -> None:
            build_dataset_access_service().on_subscription_cancelled(
                payload.get("user_id"),
                payload.get("plan_id"),
                trailing_days=payload.get("trailing_days", 0),
            )

        def on_payment_failed(_name: str, payload: dict) -> None:
            build_dataset_access_service().on_subscription_payment_failed(
                payload.get("user_id"),
                payload.get("plan_id"),
                trailing_days=payload.get("trailing_days", 0),
            )

        def on_renewed(_name: str, payload: dict) -> None:
            build_dataset_access_service().on_subscription_renewed(
                payload.get("user_id"), payload.get("plan_id")
            )

        bus.subscribe(SUBSCRIPTION_ACTIVATED, on_activated)
        bus.subscribe(SUBSCRIPTION_CANCELLED, on_cancelled)
        bus.subscribe(SUBSCRIPTION_PAYMENT_FAILED, on_payment_failed)
        bus.subscribe(SUBSCRIPTION_RENEWED, on_renewed)

        from plugins.dataset.dataset.handlers.one_time_payment_handler import (
            DatasetOneTimePaymentHandler,
        )

        one_time_handler = DatasetOneTimePaymentHandler(
            access_service_factory=build_dataset_access_service,
            invoice_lookup=_lookup_invoice_by_number,
        )
        bus.subscribe(INVOICE_PAID, one_time_handler.on_invoice_paid)

    def on_disable(self) -> None:
        """Reverse the entity-type + marketplace registrations (no-op if absent)."""
        from vbwd.services.entity_type_registry import unregister_entity_type

        unregister_entity_type(DATASET_ENTITY_TYPE)

        # Mirror of the on_enable registration — guarded soft import so the
        # source stays marketplace-free and disable is safe when absent.
        try:
            from plugins.marketplace.marketplace.services import (
                vendor_listings_registry as marketplace_listings_registry,
            )
        except ImportError:
            return
        from plugins.dataset.dataset.marketplace_listings import (
            DATASET_LISTING_TYPE_ID,
        )

        marketplace_listings_registry.unregister_vendor_listings_provider(
            DATASET_LISTING_TYPE_ID
        )
