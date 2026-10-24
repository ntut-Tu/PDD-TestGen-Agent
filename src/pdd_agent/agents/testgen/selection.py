"""Path selection: classify files, expand directories through the repository tree."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from pdd_agent.domain.errors import SelectionError
from pdd_agent.ports.source import PullFile, TreeEntry

BUILD_FILES = ("pom.xml", "build.gradle", "build.gradle.kts")
DEFAULT_TEST_PATH = "src/test/java"
_TEST_FILE = re.compile(r"(Test|Tests|IT)\.java$")
_JAVA_ROOT = re.compile(r"(?:^|.*/)src/(?:main|test)/java/(.*)$")


@dataclass(frozen=True)
class Selection:
    """Files resolved from the paths chosen by the user."""

    production: list[str]
    tests: list[str]
    targets: list[str]


def is_test_path(path: str) -> bool:
    """Return whether ``path`` lies in a ``src/test`` source root."""
    return path.startswith("src/test/") or "/src/test/" in path


def is_java_production(path: str) -> bool:
    """Return whether ``path`` is a Java source outside the test source roots."""
    return path.endswith(".java") and not is_test_path(path)


def is_java_test(path: str) -> bool:
    """Return whether ``path`` is a Java test class by naming convention."""
    return is_test_path(path) and bool(_TEST_FILE.search(path))


def package_path(path: str) -> str:
    """Return the package directory of a Java file (the directory when outside a Java root)."""
    match = _JAVA_ROOT.match(path)
    relative = match.group(1) if match else path
    return relative.rpartition("/")[0]


def expected_test_path(production_path: str) -> str | None:
    """Return the conventional test file path of a production class, if it has one."""
    if "src/main/java/" not in production_path or not production_path.endswith(".java"):
        return None
    test_path = production_path.replace("src/main/java/", "src/test/java/", 1)
    return test_path.removesuffix(".java") + "Test.java"


def find_build_file(entries: Iterable[TreeEntry]) -> str | None:
    """Return the Maven or Gradle build file at the repository root, if any."""
    roots = {e.path for e in entries if e.type == "blob"}
    return next((name for name in BUILD_FILES if name in roots), None)


def normalize_path(raw: str) -> str:
    """Normalise a user supplied repository path or raise ``SelectionError``."""
    path = raw.strip().strip("/")
    parts = path.split("/")
    if not path or "\\" in path or any(p in ("", ".", "..") for p in parts):
        raise SelectionError("invalid_path", f"invalid path {raw!r}")
    return path


def _expand(
    entries: Sequence[TreeEntry], raw_paths: Iterable[str], keep: Callable[[str], bool]
) -> tuple[list[str], list[str]]:
    """Expand files and directories; return (files, paths that match nothing)."""
    blobs = [e.path for e in entries if e.type == "blob"]
    found: set[str] = set()
    missing: list[str] = []
    for raw in raw_paths:
        path = normalize_path(raw)
        if path in blobs:
            found.add(path)
            continue
        children = [b for b in blobs if b.startswith(path + "/")]
        if not children:
            missing.append(path)
        found.update(b for b in children if keep(b))
    return sorted(found), missing


def resolve_selection(
    entries: Sequence[TreeEntry],
    changed: Sequence[PullFile],
    production_paths: Sequence[str],
    test_paths: Sequence[str],
) -> Selection:
    """Resolve the requested paths into production files, test examples and target classes.

    Production paths must exist; test paths that do not exist are ignored because a
    repository may have no tests yet. Targets are the changed production classes that
    were selected, or all selected production classes when the selection contains none.
    """
    production, missing = _expand(entries, production_paths, is_java_production)
    if missing:
        raise SelectionError("path_not_found", f"not found at the pull request head: {missing}")
    production = [p for p in production if is_java_production(p)]
    tests, _ = _expand(entries, test_paths, is_java_test)
    tests = [t for t in tests if t.endswith(".java")]
    changed_paths = {f.path for f in changed if f.status != "removed"}
    targets = [p for p in production if p in changed_paths] or production
    if not targets:
        raise SelectionError("no_target_files", "no Java production files selected")
    return Selection(production=production, tests=tests, targets=targets)


def changed_java_production(changed: Sequence[PullFile]) -> list[str]:
    """Return the Java production files added, modified or renamed by the pull request."""
    return sorted(f.path for f in changed if f.status != "removed" and is_java_production(f.path))
