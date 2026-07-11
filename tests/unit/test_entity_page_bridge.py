"""S128 — dataset's adopter of the CMS entity-page seam (unit).

Proves the guarded bridge that attaches a reusable content+SEO page to each
dataset:

  * ``register_dataset_owner_type`` registers the ``dataset`` content-owner type
    (with an ``authorize`` callback) when cms is present, and no-ops (returns
    False, never raises) when the cms import is unavailable (Liskov: the
    cms-absent path never breaks enable);
  * the ``authorize`` callback mirrors the admin dataset routes' gate — True for
    a user holding ``dataset.manage``, False otherwise / for anonymous;
  * ``delete_dataset_entity_page`` delegates to the resolved service's
    ``delete_for_owner("dataset", id)``, and is a no-op when cms is absent;
  * ``dataset_page_seo`` returns the published page's ``seo`` block, or None when
    there is no page / cms is absent.
"""
import sys

from plugins.cms.src.services import entity_page_owner_registry

from plugins.dataset.dataset.services.entity_page_bridge import (
    DATASET_MANAGE_PERMISSION,
    DATASET_OWNER_TYPE,
    delete_dataset_entity_page,
    dataset_page_seo,
    register_dataset_owner_type,
)


class _FakeUser:
    """A user stand-in whose ``has_permission`` reads a permission set."""

    def __init__(self, permissions):
        self._permissions = set(permissions)

    def has_permission(self, permission_name):
        return permission_name in self._permissions


class _FakeEntityPageService:
    """Records the delete/public_view calls the bridge makes."""

    def __init__(self, public_view_result=None):
        self.delete_calls = []
        self._public_view_result = public_view_result

    def delete_for_owner(self, owner_type, owner_id):
        self.delete_calls.append((owner_type, owner_id))

    def public_view(self, owner_type, owner_id, slot="main"):
        self.public_calls = (owner_type, owner_id, slot)
        return self._public_view_result


# ── owner-type registration ──────────────────────────────────────────────


def test_register_owner_type_registers_dataset_when_cms_present():
    entity_page_owner_registry.clear_content_owner_types()

    assert register_dataset_owner_type() is True

    owner = entity_page_owner_registry.get_content_owner_type(DATASET_OWNER_TYPE)
    assert owner is not None
    assert owner.key == "dataset"
    assert owner.label == "Dataset"


def test_register_owner_type_no_raise_when_cms_import_unavailable(monkeypatch):
    entity_page_owner_registry.clear_content_owner_types()
    # None in sys.modules makes ``from <name> import ...`` raise ImportError,
    # simulating a host where cms is not installed.
    monkeypatch.setitem(
        sys.modules,
        "plugins.cms.src.services.entity_page_owner_registry",
        None,
    )

    # Must degrade to a logged no-op (Liskov: enable never breaks), not raise.
    assert register_dataset_owner_type() is False


# ── authorize callback ───────────────────────────────────────────────────


def test_authorize_true_for_dataset_manager_false_otherwise():
    entity_page_owner_registry.clear_content_owner_types()
    register_dataset_owner_type()
    owner = entity_page_owner_registry.get_content_owner_type(DATASET_OWNER_TYPE)

    manager = _FakeUser({DATASET_MANAGE_PERMISSION})
    non_manager = _FakeUser(set())

    assert owner.authorize(manager, "any-dataset-id") is True
    assert owner.authorize(non_manager, "any-dataset-id") is False
    # Anonymous / missing user is never authorised.
    assert owner.authorize(None, "any-dataset-id") is False


def test_authorize_matches_the_admin_route_permission_literal():
    # Pin the gate to the exact permission the admin dataset routes enforce
    # (DRY without inverting the dependency arrow — mirrors VENDOR_ID_KEY).
    from plugins.dataset.dataset.routes import PERMISSION_MANAGE

    assert DATASET_MANAGE_PERMISSION == PERMISSION_MANAGE


# ── delete hook ──────────────────────────────────────────────────────────


def test_delete_hook_calls_delete_for_owner(monkeypatch):
    fake = _FakeEntityPageService()
    monkeypatch.setattr(
        "plugins.dataset.dataset.services.entity_page_bridge."
        "_resolve_entity_page_service",
        lambda: fake,
    )

    delete_dataset_entity_page("dataset-123")

    assert fake.delete_calls == [("dataset", "dataset-123")]


def test_delete_hook_is_a_no_op_when_cms_absent(monkeypatch):
    monkeypatch.setattr(
        "plugins.dataset.dataset.services.entity_page_bridge."
        "_resolve_entity_page_service",
        lambda: None,
    )

    # No service resolved (cms absent / not enabled) → silent no-op, no raise.
    delete_dataset_entity_page("dataset-123")


# ── public SEO passthrough ───────────────────────────────────────────────


def test_dataset_page_seo_returns_seo_when_page_exists(monkeypatch):
    seo = {"meta_title": "Air Quality dataset"}
    fake = _FakeEntityPageService(public_view_result={"seo": seo, "content_html": ""})
    monkeypatch.setattr(
        "plugins.dataset.dataset.services.entity_page_bridge."
        "_resolve_entity_page_service",
        lambda: fake,
    )

    assert dataset_page_seo("dataset-123") == seo
    assert fake.public_calls == ("dataset", "dataset-123", "main")


def test_dataset_page_seo_is_none_when_no_page(monkeypatch):
    fake = _FakeEntityPageService(public_view_result=None)
    monkeypatch.setattr(
        "plugins.dataset.dataset.services.entity_page_bridge."
        "_resolve_entity_page_service",
        lambda: fake,
    )

    assert dataset_page_seo("dataset-123") is None


def test_dataset_page_seo_is_none_when_cms_absent(monkeypatch):
    monkeypatch.setattr(
        "plugins.dataset.dataset.services.entity_page_bridge."
        "_resolve_entity_page_service",
        lambda: None,
    )

    assert dataset_page_seo("dataset-123") is None
