import pytest

from pdd_agent.agents.testgen.javacheck import check_path, validate_file
from tests.conftest import GOOD_TEST, TEST


def test_valid_file():
    result = validate_file(TEST, GOOD_TEST)
    assert result.syntax_valid and result.syntax_error is None


def test_syntax_error_is_reported_with_line():
    result = validate_file(TEST, "package com.acme;\nclass CalcTest {\n  void x( {\n}\n")
    assert not result.syntax_valid
    assert result.syntax_error.startswith("line ")
    assert result.content


def test_lexer_error():
    result = validate_file(TEST, 'package com.acme;\nclass CalcTest { String s = "unterminated; }')
    assert not result.syntax_valid
    assert result.syntax_error


def test_type_name_must_match_file():
    result = validate_file(TEST, "package com.acme;\nclass Other {}\n")
    assert "does not declare a type named CalcTest" in result.syntax_error


def test_package_must_match_directory():
    result = validate_file(TEST, "package wrong;\nclass CalcTest {}\n")
    assert "does not match the directory" in result.syntax_error
    assert validate_file(TEST, "class CalcTest {}\n").syntax_valid is False


def test_non_java_root_skips_package_check():
    assert validate_file("src/test/kotlin/X.java", "package any.thing;\nclass X {}\n").syntax_valid


@pytest.mark.parametrize(
    "path",
    [
        "src/main/java/com/acme/Calc.java",
        "/src/test/java/A.java",
        "src/test/../main/A.java",
        "src\\test\\java\\A.java",
        "src/test/java/A.txt",
        "pom.xml",
    ],
)
def test_paths_outside_src_test_are_rejected(path):
    assert check_path(path)
    result = validate_file(path, "class A {}")
    assert not result.syntax_valid


def test_multi_module_test_path_is_accepted():
    assert check_path("core/src/test/java/com/A.java") is None
