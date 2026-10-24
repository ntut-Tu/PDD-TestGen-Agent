from pdd_agent.agents.testgen.context import (
    TRUNCATION_MARKER,
    ContextBudget,
    ContextBundle,
    assemble_context,
    build_diff,
    order_tests_by_proximity,
)
from pdd_agent.agents.testgen.selection import Selection
from pdd_agent.ports.source import PullFile, RepoFile

A = "src/main/java/com/a/A.java"
B = "src/main/java/com/b/B.java"
T_A = "src/test/java/com/a/ATest.java"
T_B = "src/test/java/com/b/BTest.java"
T_Z = "src/test/java/org/z/ZTest.java"


def make_fetch(contents, calls=None):
    async def fetch(path):
        if calls is not None:
            calls.append(path)
        content = contents.get(path)
        return None if content is None else RepoFile(path=path, content=content)

    return fetch


def config(**kw):
    return ContextBudget(**{"max_chars": 10000, "max_files": 10, "max_file_chars": 1000, **kw})


CHANGED = [PullFile(path=A, status="modified", patch="@@ patch A")]


async def test_priority_order_and_report():
    selection = Selection(production=[B, A], tests=[T_Z, T_B, T_A], targets=[A])
    contents = {A: "class A {}", B: "class B {}", T_A: "t a", T_B: "t b", T_Z: "t z"}
    bundle = await assemble_context(make_fetch(contents), CHANGED, selection, config())
    assert bundle.included == [A, B, T_A, T_B, T_Z]
    assert bundle.omitted == []
    assert not bundle.truncated
    assert "@@ patch A" in bundle.diff


async def test_file_count_limit_skips_fetching_and_reports():
    selection = Selection(production=[A, B], tests=[T_A], targets=[A])
    calls = []
    bundle = await assemble_context(
        make_fetch({A: "a", B: "b", T_A: "t"}, calls), CHANGED, selection, config(max_files=2)
    )
    assert bundle.included == [A, B]
    assert bundle.omitted == [T_A]
    assert bundle.truncated
    assert T_A not in calls


async def test_file_truncated_to_max_file_chars():
    selection = Selection(production=[A], tests=[], targets=[A])
    bundle = await assemble_context(
        make_fetch({A: "x" * 5000}), CHANGED, selection, config(max_file_chars=500)
    )
    assert len(bundle.production[0].content) == 500
    assert bundle.production[0].content.endswith(TRUNCATION_MARKER)
    assert bundle.truncated and bundle.production[0].truncated


async def test_total_budget_cuts_then_omits():
    selection = Selection(production=[A, B], tests=[T_A], targets=[A])
    contents = {A: "a" * 900, B: "b" * 900, T_A: "t" * 900}
    cfg = config(max_chars=len(CHANGED[0].patch) + 150 + 1400, max_file_chars=1000)
    bundle = await assemble_context(make_fetch(contents), CHANGED, selection, cfg)
    sizes = [len(f.content) for f in bundle.production]
    assert sizes[0] == 900
    assert 0 < sizes[1] < 900
    assert bundle.omitted == [T_A]
    assert bundle.truncated


async def test_missing_and_binary_files_are_omitted():
    async def fetch(path):
        if path == A:
            return RepoFile(path=path, binary=True)
        return None

    selection = Selection(production=[A, B], tests=[], targets=[A])
    bundle = await assemble_context(fetch, CHANGED, selection, config())
    assert bundle.included == []
    assert bundle.omitted == [A, B]


async def test_upstream_truncated_file_marks_bundle_truncated():
    async def fetch(path):
        return RepoFile(path=path, content="x", truncated=True)

    selection = Selection(production=[A], tests=[], targets=[A])
    assert (await assemble_context(fetch, CHANGED, selection, config())).truncated


def test_diff_is_capped_per_file_and_in_total():
    files = [PullFile(path=f"f{i}", patch="p" * 300) for i in range(5)]
    diff, truncated = build_diff(files, config(max_file_chars=100, max_chars=400))
    assert truncated
    assert len(diff) == 200
    plain, plain_truncated = build_diff([PullFile(path="x", patch=None)], config())
    assert "(no textual diff)" in plain and not plain_truncated


def test_tests_ordered_by_package_proximity():
    ordered = order_tests_by_proximity([T_Z, T_B, T_A], [A])
    assert ordered == [T_A, T_B, T_Z]


def test_bundle_defaults():
    assert ContextBundle(diff="").included == []
