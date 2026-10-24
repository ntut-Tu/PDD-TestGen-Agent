import pytest

from pdd_agent.agents.testgen.selection import (
    changed_java_production,
    expected_test_path,
    find_build_file,
    is_java_test,
    normalize_path,
    package_path,
    resolve_selection,
)
from pdd_agent.domain.errors import SelectionError
from pdd_agent.ports.source import PullFile, TreeEntry


def blobs(*paths):
    return [TreeEntry(path=p, type="blob") for p in paths]


TREE = blobs(
    "pom.xml",
    "src/main/java/a/A.java",
    "src/main/java/a/B.java",
    "src/main/resources/x.properties",
    "src/test/java/a/ATest.java",
    "src/test/java/a/Helper.java",
    "src/test/java/a/BIT.java",
    "mod/src/test/java/m/MTests.java",
)


def test_find_build_file():
    assert find_build_file(TREE) == "pom.xml"
    assert find_build_file(blobs("build.gradle.kts", "build.gradle")) == "build.gradle"
    assert find_build_file(blobs("sub/pom.xml", "README.md")) is None
    assert find_build_file([TreeEntry(path="pom.xml", type="tree")]) is None


def test_directories_expand_to_java_files():
    changed = [PullFile(path="src/main/java/a/A.java", status="modified")]
    selection = resolve_selection(TREE, changed, ["src/main"], ["src/test/java", "mod"])
    assert selection.production == ["src/main/java/a/A.java", "src/main/java/a/B.java"]
    assert selection.tests == [
        "mod/src/test/java/m/MTests.java",
        "src/test/java/a/ATest.java",
        "src/test/java/a/BIT.java",
    ]
    assert selection.targets == ["src/main/java/a/A.java"]


def test_targets_fall_back_to_selection_when_nothing_changed_is_selected():
    changed = [PullFile(path="src/main/java/a/A.java", status="removed")]
    selection = resolve_selection(TREE, changed, ["src/main/java/a/B.java"], [])
    assert selection.targets == ["src/main/java/a/B.java"]
    assert selection.tests == []


def test_explicit_test_file_is_kept_even_without_test_suffix():
    selection = resolve_selection(
        TREE, [], ["src/main/java/a/A.java"], ["src/test/java/a/Helper.java"]
    )
    assert selection.tests == ["src/test/java/a/Helper.java"]


def test_missing_production_path_is_rejected_but_missing_test_path_is_ignored():
    with pytest.raises(SelectionError) as err:
        resolve_selection(TREE, [], ["src/main/java/Nope.java"], [])
    assert err.value.code == "path_not_found"
    assert resolve_selection(TREE, [], ["src/main/java/a/A.java"], ["nothing/here"]).tests == []


def test_non_java_selection_has_no_targets():
    with pytest.raises(SelectionError) as err:
        resolve_selection(TREE, [], ["src/main/resources"], [])
    assert err.value.code == "no_target_files"


@pytest.mark.parametrize("raw", ["", "/", "../x", "a/../b", "a//b", "a\\b", "./a"])
def test_invalid_paths(raw):
    with pytest.raises(SelectionError) as err:
        normalize_path(raw)
    assert err.value.code == "invalid_path"


def test_normalize_strips_slashes():
    assert normalize_path(" /src/main/ ") == "src/main"


def test_path_helpers():
    assert package_path("mod/src/main/java/com/x/Y.java") == "com/x"
    assert package_path("tools/Z.java") == "tools"
    assert expected_test_path("src/main/java/com/x/Y.java") == "src/test/java/com/x/YTest.java"
    assert expected_test_path("src/other/Y.java") is None
    assert is_java_test("src/test/java/AIT.java")
    assert not is_java_test("src/main/java/AIT.java")


def test_changed_java_production_ignores_removed_tests_and_other_files():
    changed = [
        PullFile(path="src/main/java/a/A.java", status="added"),
        PullFile(path="src/main/java/a/Gone.java", status="removed"),
        PullFile(path="src/test/java/a/ATest.java"),
        PullFile(path="README.md"),
    ]
    assert changed_java_production(changed) == ["src/main/java/a/A.java"]
