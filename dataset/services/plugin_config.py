"""Single home for reading the dataset plugin's runtime config (DRY).

Reads fresh from the shared ``config_store`` on every call (multi-worker safe,
admin changes take effect without restart) and falls back to the plugin's
``DEFAULT_CONFIG`` for any missing key. Mirrors the shop plugin's helper.
"""
from typing import Any, Dict

from flask import current_app


def dataset_config() -> Dict[str, Any]:
    """The merged dataset config: ``DEFAULT_CONFIG`` overlaid with saved values."""
    from plugins.dataset import DEFAULT_CONFIG

    merged = {**DEFAULT_CONFIG}
    config_store = getattr(current_app, "config_store", None)
    if config_store is not None:
        merged.update(config_store.get_config("dataset") or {})
    return merged


def marketplace_enabled() -> bool:
    """Whether vendor-mode (self-service vendor routes + checkout stamp) is on."""
    return bool(dataset_config().get("marketplace_enabled", False))
