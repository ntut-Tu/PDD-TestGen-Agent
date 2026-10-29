"""The dependency rules of the layered architecture, enforced with import-linter."""

import ast
import sys
from pathlib import Path

from importlinter.cli import lint_imports

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"


def test_import_contracts_hold():
    assert lint_imports(config_filename=str(PYPROJECT)) == 0


def test_sandbox_fix_agent_imports_only_domain_ports_and_agents():
    package = Path(__file__).parents[1] / "src" / "pdd_agent" / "agents" / "sandbox_fix"
    allowed = ("pdd_agent.domain", "pdd_agent.ports", "pdd_agent.agents")
    stdlib = set(sys.stdlib_module_names)
    for source in package.glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text())):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                ok = name.split(".")[0] in stdlib or name.startswith(allowed) or name == "pydantic"
                assert ok, f"{source.name} imports {name}"
