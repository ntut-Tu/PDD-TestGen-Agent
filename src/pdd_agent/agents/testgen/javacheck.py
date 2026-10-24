"""Validation of generated Java test files."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

import javalang
from javalang.parser import JavaSyntaxError
from javalang.tokenizer import LexerError

from pdd_agent.domain.jobs import GeneratedFile

_TEST_PATH = re.compile(r"^(?:.+/)?src/test/(?P<root>java/)?(?P<rest>.+\.java)$")


def check_path(path: str) -> str | None:
    """Return why ``path`` is not an acceptable generated test path, or ``None``."""
    pure = PurePosixPath(path)
    if "\\" in path or pure.is_absolute() or ".." in pure.parts:
        return "path must be relative and stay inside the repository"
    if not _TEST_PATH.match(path):
        return "path must be a .java file under src/test/"
    return None


def check_syntax(path: str, content: str) -> str | None:
    """Return a syntax or structure error of a Java test file, or ``None`` when it is valid."""
    try:
        unit = javalang.parse.parse(content)
    except JavaSyntaxError as exc:
        return _describe(exc.description, exc.at)
    except LexerError as exc:
        return f"lexical error: {exc}"
    class_name = PurePosixPath(path).stem
    if class_name not in {t.name for t in unit.types}:
        return f"file does not declare a type named {class_name}"
    expected = _expected_package(path)
    declared = unit.package.name if unit.package else ""
    if expected is not None and declared != expected:
        return f"package {declared!r} does not match the directory ({expected!r})"
    return None


def validate_file(path: str, content: str) -> GeneratedFile:
    """Check path and syntax of one generated file."""
    error = check_path(path) or check_syntax(path, content)
    return GeneratedFile(path=path, content=content, syntax_valid=error is None, syntax_error=error)


def _expected_package(path: str) -> str | None:
    match = _TEST_PATH.match(path)
    if not match or not match.group("root"):
        return None
    return ".".join(PurePosixPath(match.group("rest")).parent.parts)


def _describe(description: str, token: object) -> str:
    position = getattr(token, "position", None)
    line = getattr(position, "line", None)
    if line is None and isinstance(position, tuple):
        line = position[0]
    return f"line {line}: {description}" if line else description
