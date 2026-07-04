"""Vendor-mode contract + decoupling oracles (mirrors shop).

The money path is decoupled: dataset stamps the buyer invoice line with a LOCAL
key literal and the central ``marketplace`` plugin credits the selling vendor
from it — dataset never imports marketplace from its source dir. These tests pin
the literal (so the value can never drift from the documented ``vendor_id``
convention) and prove the dataset SOURCE dir names no ``plugins.marketplace``
import (the guarded soft import lives only in the plugin-root ``__init__.py``,
outside that dir).
"""
import os


DATASET_SOURCE_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "dataset")
)


def test_vendor_id_key_literal_is_vendor_id():
    from plugins.dataset.dataset.constants import VENDOR_ID_KEY

    # Pinned to the documented marketplace convention WITHOUT importing
    # marketplace — DRY without inverting the dependency arrow.
    assert VENDOR_ID_KEY == "vendor_id"


def _python_files(root):
    for current_dir, _dirs, files in os.walk(root):
        if "__pycache__" in current_dir:
            continue
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(current_dir, name)


def test_dataset_source_does_not_import_marketplace():
    offenders = []
    for path in _python_files(DATASET_SOURCE_ROOT):
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
        if "plugins.marketplace" in content or "from plugins import marketplace" in (
            content
        ):
            offenders.append(path)
    assert not offenders, (
        "Dataset source must not depend on the marketplace plugin — keep the "
        f"money path decoupled (stamp a literal, never import): {offenders}"
    )


def test_model_to_dict_serialises_vendor_id():
    from plugins.dataset.dataset.models.dataset import Dataset

    dataset = Dataset()
    dataset.slug = "air-quality"
    dataset.title = "Air Quality"
    # Unset vendor_id serialises as None (platform-owned).
    assert dataset.to_dict()["vendor_id"] is None

    from uuid import uuid4

    vendor_id = uuid4()
    dataset.vendor_id = vendor_id
    assert dataset.to_dict()["vendor_id"] == str(vendor_id)
