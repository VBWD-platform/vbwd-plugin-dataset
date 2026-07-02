"""T7 guard — the dataset plugin COPIES ghrm's pattern but never imports it.

Walks the dataset plugin source with the AST and asserts no ``import plugins.ghrm``
/ ``from plugins.ghrm ...`` anywhere, and that ``ghrm`` is not a declared
dependency. Mirrors the spirit of the core-agnosticism oracle so a stray ghrm
import can never sneak in.
"""
import ast
import os

import pytest

from plugins.dataset import DatasetPlugin

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def _python_sources():
    for dirpath, _dirnames, filenames in os.walk(PLUGIN_ROOT):
        if "__pycache__" in dirpath or "/tests" in dirpath:
            continue
        for filename in filenames:
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


def _ghrm_imports(source_path):
    with open(source_path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=source_path)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "plugins.ghrm"
        ):
            offenders.append(f"{source_path}: from {node.module}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("plugins.ghrm"):
                    offenders.append(f"{source_path}: import {alias.name}")
    return offenders


def test_no_ghrm_import_anywhere_in_plugin_source():
    offenders = []
    for source_path in _python_sources():
        offenders.extend(_ghrm_imports(source_path))
    assert offenders == [], f"ghrm imported by the dataset plugin: {offenders}"


def test_ghrm_is_not_a_declared_dependency():
    assert "ghrm" not in (DatasetPlugin().metadata.dependencies or [])


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
