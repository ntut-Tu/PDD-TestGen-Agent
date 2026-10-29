"""Test class names of generated files."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PurePosixPath

from pdd_agent.domain.jobs import GeneratedFile

_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)


def qualified_class_name(file: GeneratedFile) -> str:
    """Return the fully qualified name: declared package plus the file's class name."""
    match = _PACKAGE.search(file.content)
    name = PurePosixPath(file.path).stem
    return f"{match.group(1)}.{name}" if match else name


def class_names(files: Iterable[GeneratedFile]) -> list[str]:
    """Return the sorted, unique class names of ``files``."""
    return sorted({qualified_class_name(f) for f in files})
