"""T1 — plugin skeleton, registration, and lifecycle idempotency."""
from vbwd.plugins.base import BasePlugin, PluginMetadata

from plugins.dataset import DatasetPlugin
from vbwd.services.entity_type_registry import (
    clear_entity_types,
    is_registered,
)


def test_plugin_is_a_base_plugin():
    assert issubclass(DatasetPlugin, BasePlugin)


def test_metadata_declares_singular_id_and_deps_without_ghrm():
    metadata = DatasetPlugin().metadata
    assert isinstance(metadata, PluginMetadata)
    assert metadata.name == "dataset"
    assert set(metadata.dependencies) == {"subscription", "cms"}
    assert "ghrm" not in metadata.dependencies


def test_user_permissions_declare_the_dataset_set():
    keys = {perm["key"] for perm in DatasetPlugin().user_permissions}
    assert {"dataset.view", "dataset.manage", "dataset.api"} <= keys


def test_blueprint_has_no_prefix_and_serves_admin_routes():
    plugin = DatasetPlugin()
    assert plugin.get_url_prefix() == ""
    blueprint = plugin.get_blueprint()
    assert blueprint is not None
    assert blueprint.name == "dataset"


def test_on_enable_registers_entity_type_and_is_idempotent():
    clear_entity_types()
    plugin = DatasetPlugin()
    plugin.initialize({})

    plugin.on_enable()
    assert is_registered("dataset")

    # Re-enabling must not raise and must leave the registration intact.
    plugin.on_enable()
    assert is_registered("dataset")


def test_on_disable_is_a_no_op_reversal():
    clear_entity_types()
    plugin = DatasetPlugin()
    plugin.initialize({})

    # Disabled path (never enabled) must not raise (Liskov).
    plugin.on_disable()
    assert not is_registered("dataset")

    plugin.on_enable()
    plugin.on_disable()
    assert not is_registered("dataset")
