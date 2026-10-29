from pdd_agent.agents.sandbox_fix.classes import class_names, qualified_class_name
from pdd_agent.agents.sandbox_fix.prompt import (
    CONTEXT_FILE_CHARS,
    OMITTED_MARKER,
    build_fix_prompt,
    shorten_output,
)
from pdd_agent.domain.jobs import GeneratedFile
from pdd_agent.domain.sandbox import SandboxRun, SandboxSpec, SandboxStatus


def file(path: str, content: str) -> GeneratedFile:
    return GeneratedFile(path=path, content=content, syntax_valid=True)


def test_class_names_use_the_declared_package_and_the_file_name():
    a = file("src/test/java/a/b/CTest.java", "package a.b;\nclass CTest {}")
    default = file("src/test/java/DTest.java", "class DTest {}")
    assert qualified_class_name(a) == "a.b.CTest"
    assert qualified_class_name(default) == "DTest"
    assert class_names([a, default, a]) == ["DTest", "a.b.CTest"]


def test_short_output_is_kept_and_long_output_keeps_both_ends():
    assert shorten_output("short") == "short"
    long = "H" * 5000 + "M" * 20000 + "T" * 9000
    shortened = shorten_output(long)
    assert shortened.startswith("H" * 3000) and shortened.endswith("T" * 9000)
    assert OMITTED_MARKER in shortened and len(shortened) < 13000


def test_prompt_handles_a_run_without_results_and_cuts_big_context_files():
    spec = SandboxSpec(
        account="a", project_id=1, repo_id=2, head_sha="s", files=[], test_classes=[]
    )
    run = SandboxRun(
        id="r", spec=spec, status=SandboxStatus(phase="Failed", reason="compile_failed")
    )
    big = "x" * (CONTEXT_FILE_CHARS + 10)
    prompt = build_fix_prompt(
        run, [file("src/test/java/ATest.java", "t")], [("P.java", big)], "pr", 1, 2
    )
    assert "the tests did not run" in prompt.user
    assert "compile_failed" in prompt.user and "// ... truncated ..." in prompt.user
