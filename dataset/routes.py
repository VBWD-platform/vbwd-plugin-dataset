"""Dataset plugin API routes (S110 T2).

Admin catalogue management (all ``@require_permission`` gated):

    GET    /api/v1/admin/datasets                              list (view)
    POST   /api/v1/admin/datasets                              create (manage)
    GET    /api/v1/admin/datasets/<id>                         read (view)
    PUT    /api/v1/admin/datasets/<id>                         update (manage)
    DELETE /api/v1/admin/datasets/<id>                         delete (manage)
    GET    /api/v1/admin/datasets/<id>/snapshots              list (view)
    POST   /api/v1/admin/datasets/<id>/snapshots              upload (manage)
    DELETE /api/v1/admin/datasets/<id>/snapshots/<sid>        delete (manage)
    POST   /api/v1/admin/datasets/<id>/snapshots/<sid>/set-last  set last (manage)

The blueprint carries no url_prefix (``get_url_prefix()`` returns ``""``) so the
routes above use absolute paths; a public catalogue namespace is added in a
later task.
"""
import base64
import binascii
import os
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, Response, current_app, g, jsonify, request

from vbwd.events.bus import event_bus
from vbwd.extensions import db
from vbwd.middleware.api_key_auth import API_KEY_HEADER, require_api_key
from vbwd.middleware.auth import (
    require_admin,
    require_auth,
    require_permission,
    require_user_permission,
)
from vbwd.models.enums import TokenTransactionType
from vbwd.webhooks.signing import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    verify_signature,
)

from plugins.dataset.dataset.models.dataset_snapshot import INGESTED_VIA_WEBHOOK
from plugins.dataset.dataset.repositories.dataset_plan_repository import (
    DatasetPlanRepository,
)
from plugins.dataset.dataset.repositories.dataset_repository import DatasetRepository
from plugins.dataset.dataset.repositories.dataset_snapshot_repository import (
    DatasetSnapshotRepository,
)
from plugins.dataset.dataset.repositories.dataset_term_repository import (
    DatasetTermRepository,
)
from plugins.dataset.dataset.services.dataset_service import (
    DatasetNotFoundError,
    DatasetService,
    DatasetSnapshotNotFoundError,
    DEFAULT_CATEGORY_SLUG,
)
from plugins.dataset.dataset.services.dataset_preview import build_page, build_preview
from plugins.dataset.dataset.services.dataset_taxonomy_service import (
    DatasetTaxonomyService,
    InvalidCategoryTermError,
)
from plugins.dataset.dataset.services.storage.backend import DatasetStorageError
from plugins.dataset.dataset.services.storage.local_backend import LocalArchiveBackend
from plugins.dataset.dataset.services.plugin_config import marketplace_enabled
from plugins.dataset.dataset.services.storage.resolver import (
    DatasetStorageBackendResolver,
)

PERMISSION_VIEW = "dataset.view"
PERMISSION_MANAGE = "dataset.manage"

# The API-key scope the metered read endpoint demands. Core never interprets it;
# it only string-matches the key's allow-list. Single source of truth lives in
# the plugin package (declared as a user-grantable api_scope there); import it so
# the endpoints and the scope catalogue can never drift.
from plugins.dataset import DATASET_READ_SCOPE  # noqa: E402

MAX_PER_PAGE = 100
DEFAULT_PER_PAGE = 20

# Server-side preview cap — never serve more than this many rows (T8).
PREVIEW_MAX_ROWS = 100

# Hard ceiling on a single paginated rows page (never stream more per request).
PAGE_MAX_ROWS = 500

# The default number of tokens a stale/replayed webhook window spans (seconds).
DEFAULT_WEBHOOK_TIMESTAMP_TOLERANCE = 300

# Known inbound webhook sources (each with its own HMAC secret in config/var).
WEBHOOK_SOURCES = ("pipeline", "aws")

dataset_bp = Blueprint("dataset", __name__)


def _require_session_or_api_key(scope: str):
    """Accept EITHER a session JWT OR a scoped ``X-API-Key`` on a route.

    The dashboard UI calls with a session token; a programmatic client calls
    with a scoped API key. At request time this dispatches to the core API-key
    guard when the ``X-API-Key`` header is present (reusing the middleware's
    verify + scope + IP machinery — never reimplementing verification) and to
    ``require_auth`` otherwise. Both paths set ``g.user_id`` so the entitlement
    check that follows is auth-mechanism-agnostic (Liskov parity).
    """

    def decorator(view_func):
        api_key_guarded = require_api_key(scope=scope)(view_func)
        session_guarded = require_auth(view_func)

        @wraps(view_func)
        def dispatch(*args, **kwargs):
            if request.headers.get(API_KEY_HEADER):
                return api_key_guarded(*args, **kwargs)
            return session_guarded(*args, **kwargs)

        # Surface the route as authenticated to the route-exposure oracle (S90);
        # the guarded wrappers live off the dispatch chain so the marker is set
        # here explicitly, mirroring the core api-key decorator.
        dispatch.requires_auth = True  # type: ignore[attr-defined]
        return dispatch

    return decorator


def _dataset_service() -> DatasetService:
    """Composition root — repos bound to the request ``db.session``, the local
    storage backend over the core filesystem manager, and the core EventBus.

    Writes use the local backend (the MVP default); reads resolve the backend
    per snapshot via :func:`_backend_resolver` (some snapshots may live in S3).
    """
    storage_backend = LocalArchiveBackend(current_app.container.filesystem_manager())
    return DatasetService(
        dataset_repository=DatasetRepository(db.session),
        snapshot_repository=DatasetSnapshotRepository(db.session),
        storage_backend=storage_backend,
        event_bus=event_bus,
        dataset_plan_repository=DatasetPlanRepository(db.session),
    )


def _backend_resolver() -> DatasetStorageBackendResolver:
    """Per-snapshot storage-backend resolver (local default, optional AWS).

    Reads the live plugin config so the optional AWS backend picks up its
    bucket/secret from ``var`` (never hardcoded). A missing/invalid AWS secret
    degrades gracefully — local snapshots keep serving.
    """
    from plugins.dataset import _current_plugin_config

    return DatasetStorageBackendResolver(
        current_app.container.filesystem_manager(),
        config=_current_plugin_config(),
    )


def _resolve_backend_or_error(snapshot):
    """Resolve the backend holding ``snapshot``; return ``(backend, None)`` or
    ``(None, response)`` with a 503 when the backend is unavailable.

    Keeps the read routes from crashing when an AWS snapshot is requested while
    AWS is off (Liskov: local still works, the caller degrades gracefully).
    """
    try:
        return _backend_resolver().for_snapshot(snapshot), None
    except DatasetStorageError:
        return None, (
            jsonify({"error": "Storage backend unavailable", "code": "BACKEND_DOWN"}),
            503,
        )


def _taxonomy_service() -> DatasetTaxonomyService:
    """Composition root — the junction repo + the cms term repo (both bound to
    the request ``db.session``). cms is a declared hard dependency, so its
    ``TermRepository`` is always importable here.
    """
    from plugins.cms.src.repositories.term_repository import TermRepository

    return DatasetTaxonomyService(
        dataset_term_repository=DatasetTermRepository(db.session),
        term_repository=TermRepository(db.session),
    )


def _resolve_category_filter(category: str):
    """Map a ``dataset_category`` slug/id to a dataset-id filter list.

    Resolves through the ``dataset_term`` junction; an unknown category yields an
    empty id list ("no matches") rather than silently dropping the filter, so
    callers get honest, non-leaky results.
    """
    return _taxonomy_service().dataset_ids_for_category(category)


@dataset_bp.route("/api/v1/admin/datasets", methods=["GET"])
@require_auth
@require_admin
@require_permission(PERMISSION_VIEW)
def admin_list_datasets():
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(int(request.args.get("per_page", DEFAULT_PER_PAGE)), MAX_PER_PAGE)
    sort_by = request.args.get("sort_by") or "updated_at"
    sort_dir = request.args.get("sort_dir") or "desc"
    search = request.args.get("search") or None
    category = request.args.get("category") or None

    service = _dataset_service()
    dataset_ids = None
    if category:
        dataset_ids = _resolve_category_filter(category)

    result = service.list_datasets(
        page=page,
        per_page=per_page,
        sort_by=sort_by,
        sort_dir=sort_dir,
        search=search,
        dataset_ids=dataset_ids,
    )
    result["items"] = [dataset.to_dict() for dataset in result["items"]]
    return jsonify(result)


@dataset_bp.route("/api/v1/admin/datasets", methods=["POST"])
@require_auth
@require_admin
@require_permission(PERMISSION_MANAGE)
def admin_create_dataset():
    body = request.json or {}
    required = ("slug", "title")
    missing = [field for field in required if not body.get(field)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    service = _dataset_service()
    if service.slug_exists(body["slug"]):
        return jsonify({"error": "Slug already exists"}), 409

    dataset = service.create_dataset(body)
    if "tariff_plan_id" in body:
        service.set_tariff_plan_link(dataset.id, body.get("tariff_plan_id"))
    db.session.commit()
    return jsonify(dataset.to_dict()), 201


@dataset_bp.route("/api/v1/admin/datasets/<dataset_id>", methods=["GET"])
@require_auth
@require_admin
@require_permission(PERMISSION_VIEW)
def admin_get_dataset(dataset_id):
    service = _dataset_service()
    try:
        dataset = service.get_dataset(dataset_id)
    except DatasetNotFoundError:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dataset.to_dict())


@dataset_bp.route("/api/v1/admin/datasets/<dataset_id>", methods=["PUT"])
@require_auth
@require_admin
@require_permission(PERMISSION_MANAGE)
def admin_update_dataset(dataset_id):
    body = request.json or {}
    service = _dataset_service()
    try:
        dataset = service.update_dataset(dataset_id, body)
    except DatasetNotFoundError:
        return jsonify({"error": "Not found"}), 404
    if "tariff_plan_id" in body:
        service.set_tariff_plan_link(dataset.id, body.get("tariff_plan_id"))
    db.session.commit()
    return jsonify(dataset.to_dict())


@dataset_bp.route("/api/v1/admin/datasets/<dataset_id>", methods=["DELETE"])
@require_auth
@require_admin
@require_permission(PERMISSION_MANAGE)
def admin_delete_dataset(dataset_id):
    service = _dataset_service()
    try:
        service.delete_dataset(dataset_id)
    except DatasetNotFoundError:
        return jsonify({"error": "Not found"}), 404
    db.session.commit()
    return jsonify({"deleted": True})


@dataset_bp.route("/api/v1/admin/datasets/<dataset_id>/snapshots", methods=["GET"])
@require_auth
@require_admin
@require_permission(PERMISSION_VIEW)
def admin_list_snapshots(dataset_id):
    service = _dataset_service()
    try:
        snapshots = service.list_snapshots(dataset_id)
    except DatasetNotFoundError:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"items": [snapshot.to_dict() for snapshot in snapshots]})


@dataset_bp.route("/api/v1/admin/datasets/<dataset_id>/snapshots", methods=["POST"])
@require_auth
@require_admin
@require_permission(PERMISSION_MANAGE)
def admin_upload_snapshot(dataset_id):
    data, ext, taken_at, category_slug = _read_snapshot_payload()
    if data is None:
        return jsonify({"error": "No snapshot content provided"}), 400

    service = _dataset_service()
    try:
        snapshot = service.add_snapshot(
            dataset_id,
            data=data,
            ext=ext,
            taken_at=taken_at,
            category_slug=category_slug,
        )
    except DatasetNotFoundError:
        return jsonify({"error": "Not found"}), 404
    db.session.commit()
    return jsonify(snapshot.to_dict()), 201


@dataset_bp.route(
    "/api/v1/admin/datasets/<dataset_id>/snapshots/<snapshot_id>",
    methods=["DELETE"],
)
@require_auth
@require_admin
@require_permission(PERMISSION_MANAGE)
def admin_delete_snapshot(dataset_id, snapshot_id):
    service = _dataset_service()
    try:
        service.delete_snapshot(dataset_id, snapshot_id)
    except DatasetNotFoundError:
        return jsonify({"error": "Not found"}), 404
    except DatasetSnapshotNotFoundError:
        return jsonify({"error": "Snapshot not found"}), 404
    db.session.commit()
    return jsonify({"deleted": True})


@dataset_bp.route(
    "/api/v1/admin/datasets/<dataset_id>/snapshots/<snapshot_id>/set-last",
    methods=["POST"],
)
@require_auth
@require_admin
@require_permission(PERMISSION_MANAGE)
def admin_set_last_snapshot(dataset_id, snapshot_id):
    service = _dataset_service()
    try:
        dataset = service.set_last_snapshot(dataset_id, snapshot_id)
    except DatasetNotFoundError:
        return jsonify({"error": "Not found"}), 404
    except DatasetSnapshotNotFoundError:
        return jsonify({"error": "Snapshot not found"}), 404
    db.session.commit()
    return jsonify(dataset.to_dict())


@dataset_bp.route(
    "/api/v1/admin/datasets/<dataset_id>/snapshots/<snapshot_id>/download",
    methods=["GET"],
)
@require_auth
@require_admin
@require_permission(PERMISSION_VIEW)
def admin_download_snapshot(dataset_id, snapshot_id):
    """Admin download of one specific snapshot's bytes (``attachment``).

    Resolves THAT snapshot's storage backend per :func:`_backend_resolver` (a
    snapshot in S3 streams from S3, a local one from disk) and reuses the shared
    :func:`_stream_snapshot` helper. Unknown snapshot → 404; a snapshot whose
    backend is unavailable (an AWS snapshot while AWS is off) → 503.
    """
    service = _dataset_service()
    try:
        dataset = service.get_dataset(dataset_id)
        snapshot = service.get_snapshot(dataset_id, snapshot_id)
    except DatasetNotFoundError:
        return jsonify({"error": "Not found"}), 404
    except DatasetSnapshotNotFoundError:
        return jsonify({"error": "Snapshot not found"}), 404

    backend, error_response = _resolve_backend_or_error(snapshot)
    if error_response is not None:
        return error_response

    return _stream_snapshot(
        snapshot, backend, as_attachment=True, download_slug=dataset.slug
    )


@dataset_bp.route("/api/v1/admin/datasets/<dataset_id>/preview", methods=["GET"])
@require_auth
@require_admin
@require_permission(PERMISSION_VIEW)
def admin_preview_dataset(dataset_id):
    """Admin spreadsheet preview of a dataset's data (NOT entitlement-gated).

    Returns ``{"columns", "rows"}`` (the shared capped builder) for the resolved
    snapshot: the explicit ``?snapshot_id=`` when given, otherwise the dataset's
    ``last`` snapshot. A dataset with no snapshots answers ``200`` with an empty
    preview so the UI can render an empty state plus the upload control rather
    than surfacing an error. Unknown dataset → 404; unknown snapshot → 404; an
    AWS snapshot while AWS is off → the shared graceful 503.
    """
    service = _dataset_service()
    snapshot_id = request.args.get("snapshot_id")
    try:
        dataset = service.get_dataset(dataset_id)
        if snapshot_id:
            snapshot = service.get_snapshot(dataset_id, snapshot_id)
        else:
            snapshot = service.resolve_snapshot(dataset)
    except DatasetNotFoundError:
        return jsonify({"error": "Not found"}), 404
    except DatasetSnapshotNotFoundError:
        return jsonify({"error": "Snapshot not found"}), 404

    if snapshot is None:
        return jsonify({"columns": [], "rows": []})

    backend, error_response = _resolve_backend_or_error(snapshot)
    if error_response is not None:
        return error_response

    preview = build_preview(
        backend.open_stream(snapshot.location), max_rows=PREVIEW_MAX_ROWS
    )
    return jsonify(preview)


def _clamp_int(raw_value, default: int, minimum: int, maximum: int) -> int:
    """Parse ``raw_value`` to an int and clamp it to ``[minimum, maximum]``.

    A missing/garbage query value falls back to ``default`` — the paginated rows
    endpoint must never crash on a hand-typed offset/limit.
    """
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


@dataset_bp.route(
    "/api/v1/admin/datasets/<dataset_id>/snapshots/<snapshot_id>/rows",
    methods=["GET"],
)
@require_auth
@require_admin
@require_permission(PERMISSION_VIEW)
def admin_snapshot_rows(dataset_id, snapshot_id):
    """One server-paginated page of a snapshot's data (NOT entitlement-gated).

    Returns ``{"columns", "rows", "offset", "limit", "has_more"}`` for the
    ``offset``/``limit`` window, streaming only that window plus a peek line so a
    *tremendous* file is never fully loaded (no full row count is computed).
    ``limit`` is clamped to :data:`PAGE_MAX_ROWS`; a bad offset/limit falls back
    defensively. Unknown dataset/snapshot → 404; an AWS snapshot while AWS is off
    → the shared graceful 503.
    """
    service = _dataset_service()
    try:
        service.get_dataset(dataset_id)
        snapshot = service.get_snapshot(dataset_id, snapshot_id)
    except DatasetNotFoundError:
        return jsonify({"error": "Not found"}), 404
    except DatasetSnapshotNotFoundError:
        return jsonify({"error": "Snapshot not found"}), 404

    offset = _clamp_int(
        request.args.get("offset"), default=0, minimum=0, maximum=2**63 - 1
    )
    limit = _clamp_int(
        request.args.get("limit"),
        default=PREVIEW_MAX_ROWS,
        minimum=0,
        maximum=PAGE_MAX_ROWS,
    )

    backend, error_response = _resolve_backend_or_error(snapshot)
    if error_response is not None:
        return error_response

    page = build_page(
        backend.open_stream(snapshot.location), offset=offset, limit=limit
    )
    return jsonify(page)


@dataset_bp.route("/api/v1/admin/datasets/<dataset_id>/categories", methods=["GET"])
@require_auth
@require_admin
@require_permission(PERMISSION_VIEW)
def admin_list_dataset_categories(dataset_id):
    service = _dataset_service()
    try:
        service.get_dataset(dataset_id)
    except DatasetNotFoundError:
        return jsonify({"error": "Not found"}), 404
    term_ids = _taxonomy_service().assigned_category_ids(dataset_id)
    return jsonify({"term_ids": term_ids})


@dataset_bp.route("/api/v1/admin/datasets/<dataset_id>/categories", methods=["POST"])
@require_auth
@require_admin
@require_permission(PERMISSION_MANAGE)
def admin_assign_dataset_category(dataset_id):
    body = request.json or {}
    term_id = body.get("term_id")
    if not term_id:
        return jsonify({"error": "term_id is required"}), 400

    service = _dataset_service()
    try:
        service.get_dataset(dataset_id)
    except DatasetNotFoundError:
        return jsonify({"error": "Not found"}), 404

    try:
        _taxonomy_service().assign_category(dataset_id, term_id)
    except InvalidCategoryTermError as error:
        return jsonify({"error": str(error)}), 400
    db.session.commit()
    return jsonify({"assigned": True, "term_id": term_id})


@dataset_bp.route(
    "/api/v1/admin/datasets/<dataset_id>/categories/<term_id>",
    methods=["DELETE"],
)
@require_auth
@require_admin
@require_permission(PERMISSION_MANAGE)
def admin_unassign_dataset_category(dataset_id, term_id):
    service = _dataset_service()
    try:
        service.get_dataset(dataset_id)
    except DatasetNotFoundError:
        return jsonify({"error": "Not found"}), 404
    _taxonomy_service().unassign_category(dataset_id, term_id)
    db.session.commit()
    return jsonify({"unassigned": True})


@dataset_bp.route("/api/v1/admin/datasets/bulk-assign-category", methods=["POST"])
@require_auth
@require_admin
@require_permission(PERMISSION_MANAGE)
def admin_bulk_assign_category():
    body = request.json or {}
    dataset_ids = body.get("dataset_ids")
    term_id = body.get("term_id")
    if not isinstance(dataset_ids, list) or not dataset_ids:
        return jsonify({"error": "dataset_ids must be a non-empty list"}), 400
    if not term_id:
        return jsonify({"error": "term_id is required"}), 400

    try:
        assigned = _taxonomy_service().bulk_assign_category(dataset_ids, term_id)
    except InvalidCategoryTermError as error:
        return jsonify({"error": str(error)}), 400
    db.session.commit()
    return jsonify({"assigned": assigned})


def _read_snapshot_payload():
    """Extract ``(data, ext, taken_at, category_slug)`` from the request.

    Accepts either a multipart ``file`` upload or a JSON body with a ``content``
    string, so both the admin UI and API clients can post a snapshot. Returns
    ``data=None`` when no content was supplied.
    """
    uploaded = request.files.get("file")
    if uploaded is not None:
        raw = uploaded.read()
        _, dotted_ext = os.path.splitext(uploaded.filename or "")
        ext = (request.form.get("ext") or dotted_ext.lstrip(".") or "csv").lower()
        taken_at = request.form.get("taken_at") or None
        category_slug = request.form.get("category") or DEFAULT_CATEGORY_SLUG
        return raw, ext, taken_at, category_slug

    body = request.get_json(silent=True) or {}
    content = body.get("content")
    if content is None:
        return None, None, None, None
    raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    ext = (body.get("ext") or "csv").lower()
    taken_at = body.get("taken_at") or None
    category_slug = body.get("category") or DEFAULT_CATEGORY_SLUG
    return raw, ext, taken_at, category_slug


# ======================================================================
# Public catalogue (T8) — no auth, active datasets only, by category
# ======================================================================


@dataset_bp.route("/api/v1/dataset", methods=["GET"])
def public_list_datasets():
    """List the active dataset catalogue (public), optionally by category/search."""
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(int(request.args.get("per_page", DEFAULT_PER_PAGE)), MAX_PER_PAGE)
    search = request.args.get("q") or request.args.get("search") or None
    category = request.args.get("category") or None

    dataset_ids = _resolve_category_filter(category) if category else None
    result = _dataset_service().list_datasets(
        page=page,
        per_page=per_page,
        sort_by="title",
        sort_dir="asc",
        search=search,
        dataset_ids=dataset_ids,
        active_only=True,
    )
    result["items"] = [dataset.to_dict() for dataset in result["items"]]
    return jsonify(result)


# The static ``/categories`` and ``/my`` paths are declared BEFORE the
# ``/<slug>`` catalogue detail so a dataset can never be named ``categories`` or
# ``my`` and shadow them (Werkzeug prefers static rules, but the explicit order
# keeps intent obvious and is asserted by the route-ordering test).


@dataset_bp.route("/api/v1/dataset/categories", methods=["GET"])
def public_list_categories():
    """Public ``dataset_category`` index for the catalogue filter (no auth)."""
    return jsonify({"categories": _taxonomy_service().list_category_index()})


@dataset_bp.route("/api/v1/dataset/my", methods=["GET"])
@require_auth
def list_my_datasets():
    """The caller's entitled datasets (session auth; GDPR: own entitlements only).

    Unions the materialised-membership access (one-time grants + grace, via
    ``DatasetAccessService``) with the subscription-derived entitlement
    projection (``IDatasetEntitlements``), mirroring :func:`_user_is_entitled`
    (DRY — no access logic is duplicated here). Returns a bare array of public
    catalogue dicts (the fe "My datasets" contract).
    """
    from plugins.dataset import (
        build_dataset_access_service,
        build_dataset_entitlement_service,
    )

    entitled_ids = {
        str(dataset_id)
        for dataset_id in build_dataset_entitlement_service().active_dataset_ids(
            g.user_id
        )
    }
    entitled_ids |= {
        str(dataset_id)
        for dataset_id in build_dataset_access_service().active_dataset_ids(g.user_id)
    }
    if not entitled_ids:
        return jsonify([])

    result = _dataset_service().list_datasets(
        page=1,
        per_page=len(entitled_ids),
        dataset_ids=list(entitled_ids),
    )
    return jsonify([dataset.to_dict() for dataset in result["items"]])


@dataset_bp.route("/api/v1/dataset/<slug>", methods=["GET"])
def public_get_dataset(slug):
    """Public catalogue detail for one dataset (no auth, no location/data leak).

    Returns the dataset's public catalogue fields via ``to_dict`` — which never
    includes the raw storage ``location`` (that lives on the snapshot) nor the
    data itself. 404 when the slug is unknown.
    """
    service = _dataset_service()
    dataset = _resolve_dataset_or_none(service, slug)
    if dataset is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dataset.to_dict())


# ======================================================================
# One-time order (T6 differentiator) — CUSTOM dataset invoice line
# ======================================================================


@dataset_bp.route("/api/v1/dataset/orders", methods=["POST"])
@require_auth
def create_dataset_order():
    """Create a one-time dataset purchase invoice (a CUSTOM dataset line).

    The one-time counterpart to the recurring subscription line item: it creates
    a PENDING invoice with a single ``LineItemType.CUSTOM`` line tagged
    ``plugin='dataset'`` (so the fe-user invoice detail resolves it to the
    dataset access page) and stamps ``payment_metadata.dataset`` — the shape the
    ``invoice.paid`` one-time handler reads to grant access.

    A zero-price order is captured immediately (grant fires on ``invoice.paid``);
    a funded order is left PENDING for the buyer's selected payment method (e.g.
    token-balance) to capture through the existing checkout flow — the same
    ``invoice.paid`` handler grants access whichever provider captures.
    """
    from decimal import Decimal

    from vbwd.plugins.payment_route_helpers import emit_payment_captured

    from plugins.dataset.dataset.services.dataset_order_service import (
        DatasetOrderService,
    )

    body = request.json or {}
    slug = body.get("dataset_slug") or body.get("slug")
    if not slug:
        return jsonify({"error": "dataset_slug is required"}), 400

    service = _dataset_service()
    dataset = _resolve_dataset_or_none(service, slug)
    if dataset is None:
        return jsonify({"error": "Not found"}), 404

    order_service = DatasetOrderService(
        db.session, price_factory=current_app.container.price_factory()
    )
    invoice = order_service.create_one_time_order(g.user_id, dataset)
    db.session.commit()

    if (invoice.total_amount or Decimal("0")) <= Decimal("0"):
        emit_payment_captured(
            invoice_id=invoice.id,
            payment_reference=f"zero-price:{invoice.id}",
            amount=invoice.total_amount,
            currency=invoice.currency,
            provider="zero-price",
            transaction_id=str(invoice.id),
        )

    return (
        jsonify(
            {
                "invoice_id": str(invoice.id),
                "invoice_number": invoice.invoice_number,
                "status": invoice.status.value,
                "total_amount": str(invoice.total_amount),
                "currency": invoice.currency,
            }
        ),
        201,
    )


# ======================================================================
# Vendor: Self-service (marketplace vendor-mode) — S113 parity with shop
# ======================================================================
#
# Gated behind the ``marketplace_enabled`` config flag AND the user-facing
# ``marketplace.vendor`` permission. A vendor owns the datasets they create
# (``vendor_id`` = their user id) and may only edit their own. When vendor-mode
# is off these routes return 403 (classic single-owner catalogue). The
# permission is the central marketplace plugin's convention; dataset never
# imports marketplace.


def _require_marketplace_enabled():
    """Return a 403 response tuple when vendor-mode is off, else ``None``."""
    if not marketplace_enabled():
        return jsonify({"error": "Vendor mode is not enabled"}), 403
    return None


def _load_owned_dataset(dataset_id):
    """Load a dataset and enforce vendor ownership (``vendor_id == g.user_id``).

    The single home for the vendor dataset-scoped guard — returns
    ``(dataset, None)`` or ``(None, error_tuple)`` (404 when missing, 403 when
    owned by another vendor).
    """
    dataset = DatasetRepository(db.session).find_by_id(dataset_id)
    if not dataset:
        return None, (jsonify({"error": "Dataset not found"}), 404)
    if str(dataset.vendor_id) != str(g.user_id):
        return None, (jsonify({"error": "You do not own this dataset"}), 403)
    return dataset, None


@dataset_bp.route("/api/v1/dataset/vendor/datasets", methods=["POST"])
@require_auth
@require_user_permission("marketplace.vendor")
def vendor_create_dataset():
    """Vendor self-service: create a dataset the calling vendor owns."""
    disabled = _require_marketplace_enabled()
    if disabled:
        return disabled

    body = request.json or {}
    required = ("slug", "title")
    missing = [field for field in required if not body.get(field)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    service = _dataset_service()
    if service.slug_exists(body["slug"]):
        return jsonify({"error": "Slug already exists"}), 409

    dataset = service.create_dataset(body)
    # Stamp ownership explicitly — ``vendor_id`` is NOT a writable field, so a
    # buyer/admin can never set it via payload (mirrors shop).
    dataset.vendor_id = g.user_id
    db.session.commit()
    return jsonify({"dataset": dataset.to_dict()}), 201


@dataset_bp.route("/api/v1/dataset/vendor/datasets", methods=["GET"])
@require_auth
@require_user_permission("marketplace.vendor")
def vendor_list_datasets():
    """Vendor self-service: list ONLY the calling vendor's own datasets."""
    disabled = _require_marketplace_enabled()
    if disabled:
        return disabled

    datasets = DatasetRepository(db.session).find_by_vendor_id(g.user_id)
    return jsonify({"datasets": [dataset.to_dict() for dataset in datasets]}), 200


@dataset_bp.route("/api/v1/dataset/vendor/datasets/<dataset_id>", methods=["GET"])
@require_auth
@require_user_permission("marketplace.vendor")
def vendor_get_dataset(dataset_id):
    """Vendor self-service: read a dataset the calling vendor owns (else 403)."""
    disabled = _require_marketplace_enabled()
    if disabled:
        return disabled

    dataset, error = _load_owned_dataset(dataset_id)
    if error:
        return error
    return jsonify({"dataset": dataset.to_dict()}), 200


@dataset_bp.route("/api/v1/dataset/vendor/datasets/<dataset_id>", methods=["PUT"])
@require_auth
@require_user_permission("marketplace.vendor")
def vendor_update_dataset(dataset_id):
    """Vendor self-service: edit a dataset the calling vendor owns (else 403)."""
    disabled = _require_marketplace_enabled()
    if disabled:
        return disabled

    dataset, error = _load_owned_dataset(dataset_id)
    if error:
        return error

    body = request.json or {}
    service = _dataset_service()
    dataset = service.update_dataset(dataset.id, body)
    db.session.commit()
    return jsonify({"dataset": dataset.to_dict()}), 200


@dataset_bp.route("/api/v1/dataset/vendor/datasets/<dataset_id>", methods=["DELETE"])
@require_auth
@require_user_permission("marketplace.vendor")
def vendor_delete_dataset(dataset_id):
    """Vendor self-service: delete a dataset the calling vendor owns (else 403)."""
    disabled = _require_marketplace_enabled()
    if disabled:
        return disabled

    dataset, error = _load_owned_dataset(dataset_id)
    if error:
        return error

    _dataset_service().delete_dataset(dataset.id)
    db.session.commit()
    return jsonify({"success": True}), 200


# ======================================================================
# Scoped read API (T8) — metered `data`, plus session preview/meta/download
# ======================================================================


@dataset_bp.route("/api/v1/dataset/<slug>/data", methods=["GET"])
@require_api_key(scope=DATASET_READ_SCOPE)
def read_dataset_data(slug):
    """Serve the ``last`` snapshot (or a pinned ``?taken_at=``), metered per call.

    Gated by a scoped API key AND the caller's dataset entitlement; each call
    debits the caller's token balance (over quota → 429). The bytes are streamed
    through the storage backend — the raw ``location`` is never exposed.
    """
    service = _dataset_service()
    dataset = _resolve_dataset_or_none(service, slug)
    if dataset is None:
        return jsonify({"error": "Not found"}), 404
    if not _user_is_entitled(g.user_id, dataset):
        return jsonify({"error": "Not entitled to this dataset"}), 403

    snapshot = service.resolve_snapshot(dataset, request.args.get("taken_at"))
    if snapshot is None:
        return jsonify({"error": "No data available"}), 404

    backend, error_response = _resolve_backend_or_error(snapshot)
    if error_response is not None:
        return error_response

    if not _meter_api_call("data"):
        return jsonify({"error": "Usage limit exceeded", "code": "LIMIT_EXCEEDED"}), 429
    db.session.commit()

    return _stream_snapshot(snapshot, backend, as_attachment=False)


@dataset_bp.route("/api/v1/dataset/<slug>/download", methods=["GET"])
@require_auth
def download_dataset_data(slug):
    """Browser download of the ``last`` snapshot (session auth, entitlement-gated).

    Not metered — this is the dashboard "Download" button, which must work
    without an API key. Sets ``Content-Disposition: attachment``.
    """
    service = _dataset_service()
    dataset = _resolve_dataset_or_none(service, slug)
    if dataset is None:
        return jsonify({"error": "Not found"}), 404
    if not _user_is_entitled(g.user_id, dataset):
        return jsonify({"error": "Not entitled to this dataset"}), 403

    snapshot = service.resolve_snapshot(dataset, request.args.get("taken_at"))
    if snapshot is None:
        return jsonify({"error": "No data available"}), 404

    backend, error_response = _resolve_backend_or_error(snapshot)
    if error_response is not None:
        return error_response

    return _stream_snapshot(
        snapshot, backend, as_attachment=True, download_slug=dataset.slug
    )


@dataset_bp.route("/api/v1/dataset/<slug>/snapshots", methods=["GET"])
@_require_session_or_api_key(scope=DATASET_READ_SCOPE)
def list_dataset_snapshots(slug):
    """List a dataset's archived snapshot versions (newest first).

    Dual auth (session JWT or scoped API key) + entitlement-gated. Returns the
    public per-version fields only — never the raw storage ``location`` — and
    flags the dataset's current ``last`` snapshot with ``is_last``.
    """
    service = _dataset_service()
    dataset = _resolve_dataset_or_none(service, slug)
    if dataset is None:
        return jsonify({"error": "Not found"}), 404
    if not _user_is_entitled(g.user_id, dataset):
        return jsonify({"error": "Not entitled to this dataset"}), 403

    snapshots = service.list_snapshots(str(dataset.id))
    return jsonify(
        {
            "snapshots": [
                _snapshot_version_dict(snapshot, dataset.last_snapshot_id)
                for snapshot in snapshots
            ],
            "total": len(snapshots),
        }
    )


@dataset_bp.route(
    "/api/v1/dataset/<slug>/snapshots/<snapshot_id>/download", methods=["GET"]
)
@_require_session_or_api_key(scope=DATASET_READ_SCOPE)
def download_dataset_snapshot(slug, snapshot_id):
    """Download one specific archived snapshot version as an attachment.

    Dual auth (session JWT or scoped API key) + entitlement-gated, not metered
    (mirrors ``/download``). 404 when the snapshot is unknown or belongs to a
    different dataset (never serves another dataset's bytes).
    """
    service = _dataset_service()
    dataset = _resolve_dataset_or_none(service, slug)
    if dataset is None:
        return jsonify({"error": "Not found"}), 404
    if not _user_is_entitled(g.user_id, dataset):
        return jsonify({"error": "Not entitled to this dataset"}), 403

    try:
        snapshot = service.get_snapshot(str(dataset.id), snapshot_id)
    except DatasetSnapshotNotFoundError:
        return jsonify({"error": "Snapshot not found"}), 404

    backend, error_response = _resolve_backend_or_error(snapshot)
    if error_response is not None:
        return error_response

    return _stream_snapshot(
        snapshot, backend, as_attachment=True, download_slug=dataset.slug
    )


@dataset_bp.route("/api/v1/dataset/<slug>/preview", methods=["GET"])
@require_auth
def preview_dataset(slug):
    """Return the first ``PREVIEW_MAX_ROWS`` rows as ``{columns, rows}``.

    Entitlement-gated, session auth, not metered. The cap is enforced at read
    time (the stream is only pulled until the cap is reached), so a large file is
    never fully loaded to slice its first rows.
    """
    service = _dataset_service()
    dataset = _resolve_dataset_or_none(service, slug)
    if dataset is None:
        return jsonify({"error": "Not found"}), 404
    if not _user_is_entitled(g.user_id, dataset):
        return jsonify({"error": "Not entitled to this dataset"}), 403

    snapshot = service.resolve_snapshot(dataset, request.args.get("taken_at"))
    if snapshot is None:
        return jsonify({"error": "No data available"}), 404

    backend, error_response = _resolve_backend_or_error(snapshot)
    if error_response is not None:
        return error_response

    preview = build_preview(
        backend.open_stream(snapshot.location), max_rows=PREVIEW_MAX_ROWS
    )
    return jsonify(preview)


@dataset_bp.route("/api/v1/dataset/<slug>/meta", methods=["GET"])
@require_auth
def dataset_meta(slug):
    """Return the ``last`` snapshot's issue metadata (entitlement-gated)."""
    service = _dataset_service()
    dataset = _resolve_dataset_or_none(service, slug)
    if dataset is None:
        return jsonify({"error": "Not found"}), 404
    if not _user_is_entitled(g.user_id, dataset):
        return jsonify({"error": "Not entitled to this dataset"}), 403

    snapshot = service.resolve_snapshot(dataset, request.args.get("taken_at"))
    if snapshot is None:
        return jsonify({"error": "No data available"}), 404

    return jsonify(
        {
            "dataset_slug": dataset.slug,
            "title": dataset.title,
            "source_attribution": dataset.source_attribution,
            "taken_at": snapshot.taken_at,
            "size_bytes": snapshot.size_bytes,
            "checksum": snapshot.checksum,
            "ext": snapshot.ext,
            "storage_backend": snapshot.storage_backend,
        }
    )


# ======================================================================
# Protected inbound ingest webhooks (T9) — HMAC-signed, replay-guarded
# ======================================================================


@dataset_bp.route("/api/v1/dataset/webhooks/<source>/ingest", methods=["POST"])
def webhook_ingest_snapshot(source):
    """Ingest a new snapshot from a signed pipeline/S3 push and advance ``last``.

    HMAC-signature-verified per-source secret + a timestamp/replay guard — NOT
    permission-gated (machine-to-machine). A valid signature creates a
    ``DatasetSnapshot`` (via the shared archive service so ``last``/events stay
    DRY) and emits ``dataset.updated``.
    """
    raw_body = request.get_data()
    if not _verify_webhook_signature(source, raw_body):
        return jsonify({"error": "Invalid or missing signature"}), 401

    body = request.get_json(silent=True) or {}
    slug = body.get("dataset_slug") or body.get("slug")
    if not slug:
        return jsonify({"error": "dataset_slug is required"}), 400

    service = _dataset_service()
    dataset = _resolve_dataset_or_none(service, slug)
    if dataset is None:
        return jsonify({"error": "Unknown dataset"}), 404

    data = _decode_webhook_content(body)
    if data is None:
        return jsonify({"error": "No snapshot content provided"}), 400

    snapshot = service.add_snapshot(
        str(dataset.id),
        data=data,
        ext=(body.get("ext") or "csv").lower(),
        taken_at=body.get("taken_at") or None,
        category_slug=body.get("category") or DEFAULT_CATEGORY_SLUG,
        ingested_via=INGESTED_VIA_WEBHOOK,
    )
    db.session.commit()
    return jsonify(snapshot.to_dict()), 201


# ======================================================================
# Read-path helpers (entitlement, metering, streaming, webhook verify)
# ======================================================================


def _resolve_dataset_or_none(service: DatasetService, slug: str):
    """Resolve a dataset by slug, returning ``None`` when it does not exist."""
    try:
        return service.get_dataset_by_slug(slug)
    except DatasetNotFoundError:
        return None


def _user_is_entitled(user_id, dataset) -> bool:
    """True when the user may access ``dataset`` right now.

    Reuses the T7 access services (DRY): the materialised membership (covers
    one-time grants + grace) OR the subscription-derived ``IDatasetEntitlements``
    projection. No access logic is duplicated here.
    """
    from plugins.dataset import (
        build_dataset_access_service,
        build_dataset_entitlement_service,
    )

    if build_dataset_access_service().has_active_access(user_id, dataset.id):
        return True
    entitled_ids = build_dataset_entitlement_service().active_dataset_ids(user_id)
    return str(dataset.id) in {str(candidate) for candidate in entitled_ids}


def _meter_api_call(endpoint_key: str) -> bool:
    """Debit the caller's token balance for one metered read.

    Returns ``False`` when the balance cannot cover the call (over quota → 429).
    The per-endpoint weight is ops-tunable config (never hardcoded); a zero
    weight means the endpoint is not metered. Mirrors meinchat's USAGE debit.
    """
    from plugins.dataset import _current_plugin_config

    config = _current_plugin_config()
    weights = config.get("api_endpoint_weights") or {}
    weight = int(weights.get(endpoint_key, 0))
    if weight <= 0:
        return True

    token_service = current_app.container.token_service()
    try:
        token_service.debit_tokens(
            g.user_id,
            weight,
            TokenTransactionType.USAGE,
            description=f"dataset API: {endpoint_key}",
        )
    except ValueError:
        return False
    return True


def _snapshot_version_dict(snapshot, last_snapshot_id) -> dict:
    """The entitled-user view of one snapshot version (no raw ``location``).

    Exposes the archive metadata a caller needs to pick a version and flags the
    dataset's current ``last`` snapshot with ``is_last``.
    """
    return {
        "id": str(snapshot.id),
        "taken_at": snapshot.taken_at,
        "size_bytes": snapshot.size_bytes,
        "ext": snapshot.ext,
        "checksum": snapshot.checksum,
        "storage_backend": snapshot.storage_backend,
        "is_last": bool(last_snapshot_id) and str(snapshot.id) == str(last_snapshot_id),
    }


def _stream_snapshot(
    snapshot, backend, *, as_attachment: bool, download_slug: str = ""
):
    """Stream a snapshot's bytes through its storage backend (never a raw path).

    ``backend`` is resolved per snapshot by :func:`_resolve_backend_or_error`, so
    a snapshot stored in S3 streams from S3 and a local one from disk.
    """
    mimetype = "text/csv" if snapshot.ext == "csv" else "application/octet-stream"
    response = Response(backend.open_stream(snapshot.location), mimetype=mimetype)
    if as_attachment:
        filename = f"{download_slug or 'dataset'}-{snapshot.taken_at}.{snapshot.ext}"
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _verify_webhook_signature(source: str, raw_body: bytes) -> bool:
    """Constant-time HMAC verify + replay guard for an inbound ingest webhook.

    The signature covers ``"<timestamp>." + body`` (reusing the core HMAC helper)
    so a replay must reuse its original — now stale — timestamp and is rejected by
    the freshness window. An unknown/unconfigured source has no secret and fails.
    """
    from plugins.dataset import _current_plugin_config

    if source not in WEBHOOK_SOURCES:
        return False

    config = _current_plugin_config()
    secret = (config.get("webhook_secrets") or {}).get(source)
    if not secret:
        return False

    provided_signature = request.headers.get(SIGNATURE_HEADER)
    provided_timestamp = request.headers.get(TIMESTAMP_HEADER)
    if not provided_signature or not provided_timestamp:
        return False
    if not _webhook_timestamp_is_fresh(provided_timestamp, config):
        return False

    signed_payload = f"{provided_timestamp}.".encode("utf-8") + raw_body
    return verify_signature(secret, signed_payload, provided_signature)


def _webhook_timestamp_is_fresh(provided_timestamp: str, config: dict) -> bool:
    """True when ``provided_timestamp`` (unix seconds) is within the tolerance."""
    tolerance = int(
        config.get(
            "webhook_timestamp_tolerance_seconds", DEFAULT_WEBHOOK_TIMESTAMP_TOLERANCE
        )
    )
    try:
        timestamp = int(float(provided_timestamp))
    except (TypeError, ValueError):
        return False
    now = int(datetime.now(timezone.utc).timestamp())
    return abs(now - timestamp) <= tolerance


def _decode_webhook_content(body: dict):
    """Extract the snapshot bytes from a webhook body (base64 or text)."""
    encoded = body.get("content_base64")
    if encoded is not None:
        try:
            return base64.b64decode(encoded)
        except (ValueError, binascii.Error):
            return None
    content = body.get("content")
    if content is None:
        return None
    return content.encode("utf-8") if isinstance(content, str) else bytes(content)
