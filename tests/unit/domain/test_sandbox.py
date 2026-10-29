from pdd_agent.domain.sandbox import ClassResult, SandboxResult, SandboxStatus, summarize


def test_summary_counts_failures_and_errors():
    result = SandboxResult(
        compiled=True,
        tests=[
            ClassResult(**{"class": "a.A", "passed": 2, "failed": 1}),
            ClassResult(**{"class": "a.B", "passed": 1, "errors": 2}),
        ],
    )
    status = SandboxStatus(phase="Failed", message="tests failed", result=result)
    assert summarize(status) == "tests failed (3 passed, 3 failed or errored)"
    assert summarize(status.model_copy(update={"message": ""})) == "3 passed, 3 failed or errored"


def test_summary_without_test_results_is_the_message():
    assert summarize(SandboxStatus(phase="Error", message="no runsc")) == "no runsc"
    empty = SandboxStatus(phase="Failed", message="compile", result=SandboxResult())
    assert summarize(empty) == "compile"
