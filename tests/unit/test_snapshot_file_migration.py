"""S124 — the snapshot-file migration anchors on the plugin's own prior head.

Keeps the dataset migration chain resolvable with the dataset plugin alone (core
stays standalone-resolvable) and pins the table/index/constraint names so the
up -> down -> up validation stays deterministic.
"""
import importlib


def _load_migration():
    return importlib.import_module(
        "plugins.dataset.migrations.versions." "20260707_1000_dataset_snapshot_file"
    )


def test_revision_and_down_revision_chain():
    module = _load_migration()
    assert module.revision == "20260707_1000_dataset_snapshot_file"
    # Anchored on the dataset plugin's own current head, NOT a core revision.
    assert module.down_revision == "20260704_1000_dataset_vendor_id"


def test_table_index_and_fk_names():
    module = _load_migration()
    assert module._TABLE == "dataset_snapshot_file"
    assert module._INDEX == "ix_dataset_snapshot_file_snapshot_id"
    assert module._FK == "fk_dataset_snapshot_file_snapshot_id"
