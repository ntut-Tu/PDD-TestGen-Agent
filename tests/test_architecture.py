"""The dependency rules of the layered architecture, enforced with import-linter."""

from pathlib import Path

from importlinter.cli import lint_imports

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"


def test_import_contracts_hold():
    assert lint_imports(config_filename=str(PYPROJECT)) == 0
