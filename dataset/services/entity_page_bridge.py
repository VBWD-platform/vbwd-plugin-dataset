"""CMS entity-page bridge — dataset's adopter of the S128 entity-page seam.

Dataset attaches a reusable content+SEO page to each dataset via the CMS
entity-page capability. CMS is a *declared but runtime-optional* dependency
(``PluginMetadata.dependencies`` lists it, yet the plugin must still enable and
serve when cms is absent — Liskov), so every reach into cms is guarded HERE and
degrades to a logged no-op / None. This is the single home for the entity-page
seam (DRY); the concrete service is resolved through the DI container, never
imported at module top-level (DIP + keeps a cms-absent import from breaking the
plugin's own import).
"""
import logging
from typing import Any, Dict, Optional

# The owner-type key + label this plugin contributes to the cms entity-page
# owner registry.
DATASET_OWNER_TYPE = "dataset"
DATASET_OWNER_LABEL = "Dataset"

# The permission the ``authorize`` callback checks — the exact gate the admin
# dataset routes enforce (``@require_permission("dataset.manage")``). A contract
# test pins this to ``routes.PERMISSION_MANAGE`` (DRY without a circular import).
DATASET_MANAGE_PERMISSION = "dataset.manage"

# The DI provider cms registers for its entity-page service (cms ``on_enable``).
_ENTITY_PAGE_SERVICE_PROVIDER = "cms_entity_page_service"

_logger = logging.getLogger(__name__)


def _authorize_dataset_page(user: Any, owner_id: str) -> bool:
    """May ``user`` edit dataset ``owner_id``'s page?

    Reuses the exact gate the admin dataset routes enforce — the
    ``dataset.manage`` permission (``g.user.has_permission`` inside
    ``require_permission``). The admin entity-page routes already
    ``require_admin``; this callback is the finer per-owner gate on top. A
    missing/anonymous user is never authorised.
    """
    if user is None:
        return False
    has_permission = getattr(user, "has_permission", None)
    if not callable(has_permission):
        return False
    return bool(has_permission(DATASET_MANAGE_PERMISSION))


def register_dataset_owner_type() -> bool:
    """Register the ``dataset`` content-owner type with the cms registry.

    Guarded: cms is runtime-optional, so a missing cms import logs and returns
    False (the plugin still enables — Liskov). Idempotent: the registry replaces
    by key. Returns True when the owner type was registered.
    """
    try:
        from plugins.cms.src.services.entity_page_owner_registry import (
            ContentOwnerType,
            register_content_owner_type,
        )
    except ImportError as import_error:
        _logger.warning(
            "[dataset] Entity-page owner type not registered (cms absent?): %s",
            import_error,
        )
        return False

    register_content_owner_type(
        ContentOwnerType(
            key=DATASET_OWNER_TYPE,
            label=DATASET_OWNER_LABEL,
            authorize=_authorize_dataset_page,
        )
    )
    return True


def _resolve_entity_page_service() -> Optional[Any]:
    """Resolve the cms entity-page service from the DI container, or None.

    cms registers ``container.cms_entity_page_service`` in its ``on_enable``; a
    cms-absent (or not-yet-enabled) host has no such provider, so this returns
    None and callers degrade to a no-op. Resolving through the container (never
    importing the concrete service) keeps the dependency inverted (DIP).
    """
    from flask import current_app

    container = getattr(current_app, "container", None)
    if container is None:
        return None
    provider = getattr(container, _ENTITY_PAGE_SERVICE_PROVIDER, None)
    if provider is None:
        return None
    return provider()


def delete_dataset_entity_page(dataset_id: Any) -> None:
    """Delete the entity page(s) attached to a deleted dataset (guarded).

    Called from the dataset delete path so the attached cms page + link are
    cleaned up. A cms-absent host is a no-op (Liskov: the dataset delete still
    succeeds); a failure in cleanup is logged, never re-raised, so it cannot
    abort the owning delete transaction.
    """
    service = _resolve_entity_page_service()
    if service is None:
        return
    try:
        service.delete_for_owner(DATASET_OWNER_TYPE, str(dataset_id))
    except Exception as delete_error:  # cleanup must not abort the dataset delete
        _logger.warning(
            "[dataset] Failed to delete entity page for dataset %s: %s",
            dataset_id,
            delete_error,
        )


def dataset_page_seo(dataset_id: Any) -> Optional[Dict[str, Any]]:
    """Return the attached entity page's published SEO block, or None.

    Sourced from ``public_view("dataset", id).seo`` so the public dataset detail
    can emit the page's SEO. None when cms is absent, no page exists, or the page
    is unpublished — the caller omits ``page_seo`` in that case.
    """
    service = _resolve_entity_page_service()
    if service is None:
        return None
    try:
        view = service.public_view(DATASET_OWNER_TYPE, str(dataset_id))
    except Exception as view_error:  # a SEO read must never 500 the public page
        _logger.warning(
            "[dataset] Failed to read entity page SEO for dataset %s: %s",
            dataset_id,
            view_error,
        )
        return None
    if not view:
        return None
    return view.get("seo")
