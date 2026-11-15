# Vendored from maci0/home-assistant-sp v1.8.0 (25833b6), MIT licensed,
# Copyright (c) 2026 Marcel W. Wysocki. See
# custom_components/singapore/sp_account/LICENSE.
"""The pure modules import no Home Assistant code and only lower layers."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = (
    Path(__file__).resolve().parents[2]
    / "custom_components"
    / "singapore"
    / "sp_account"
)

# Each module may import only the siblings listed for it.
ALLOWED_SIBLINGS = {
    "const": set(),
    "models": {"const"},
    "history": {"const", "models"},
    "mapper": {"const", "models", "history"},
    "client": {"const", "models"},
}


def _sibling_imports(module: str) -> set[str]:
    tree = ast.parse((PACKAGE / f"{module}.py").read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            names.add(node.module)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
    return names


@pytest.mark.parametrize("module", sorted(ALLOWED_SIBLINGS))
def test_no_homeassistant_import(module: str) -> None:
    assert "homeassistant" not in _sibling_imports(module)


@pytest.mark.parametrize("module", sorted(ALLOWED_SIBLINGS))
def test_only_lower_layers_imported(module: str) -> None:
    package_modules = {path.stem for path in PACKAGE.glob("*.py")}
    imported = _sibling_imports(module) & package_modules
    assert imported <= ALLOWED_SIBLINGS[module]
