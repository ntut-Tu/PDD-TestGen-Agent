from datetime import timedelta
from pathlib import Path

import pytest

from pdd_agent.config import ConfigError, deep_merge, load_settings, parse_duration

REQUIRED = "pdd: {jwtSecret: s}\ngitGateway: {internalToken: t}\nllm: {apiKey: k}\n"


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def test_defaults_report_all_missing_required_values_together():
    with pytest.raises(ConfigError) as err:
        load_settings()
    assert err.value.errors == [
        "pdd.jwtSecret: required",
        "gitGateway.internalToken: required",
        "llm.apiKey: required",
    ]


def test_files_deep_merge_in_order(tmp_path):
    first = write(
        tmp_path, "a.yaml", REQUIRED + "llm: {apiKey: k, model: m1}\ncontext: {maxFiles: 5}\n"
    )
    second = write(tmp_path, "b.yaml", "llm: {model: m2}\ncontext: {maxChars: 99}\n")
    settings = load_settings([first, second])
    assert settings.llm.model == "m2"
    assert settings.llm.api_key == "k"
    assert settings.context.max_files == 5
    assert settings.context.max_chars == 99
    assert settings.server.listen_addr == "0.0.0.0:8000"
    assert settings.pdd.backend.cache_ttl == timedelta(seconds=30)


def test_validation_errors_name_yaml_paths(tmp_path):
    bad = write(
        tmp_path,
        "bad.yaml",
        REQUIRED
        + """
server: {listenAddr: nope, logLevel: loud, extra: 1}
pdd: {jwtSecret: s, backend: {url: ftp, timeout: soon, insecureTLS: "yes"}}
llm: {provider: openai, baseURL: ftp, apiKey: 5, requestTimeout: 0s}
context: {maxChars: 0, maxFiles: "x"}
jobs: 3
""",
    )
    with pytest.raises(ConfigError) as err:
        load_settings([bad])
    errors = "\n".join(err.value.errors)
    for expected in (
        "server.listenAddr: must be host:port",
        "server.logLevel: must be one of",
        "server.extra: unknown key",
        "pdd.backend.url: must be an http(s) URL",
        "pdd.backend.timeout: invalid duration 'soon'",
        "pdd.backend.insecureTLS: must be true or false",
        "llm.provider: must be one of google_genai",
        "llm.baseURL: must be an http(s) URL",
        "llm.apiKey: must be a string",
        "llm.requestTimeout: must be greater than zero",
        "context.maxChars: must be greater than zero",
        "context.maxFiles: must be an integer",
        "jobs: must be a mapping",
    ):
        assert expected in errors


def test_unreadable_and_malformed_files(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_settings([tmp_path / "missing.yaml"])
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_settings([write(tmp_path, "x.yaml", "a: [")])
    with pytest.raises(ConfigError, match="top level must be a mapping"):
        load_settings([write(tmp_path, "y.yaml", "- 1")])
    assert load_settings([write(tmp_path, "empty.yaml", REQUIRED), write(tmp_path, "e2.yaml", "")])


def test_non_string_duration_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="duration string"):
        load_settings(
            [write(tmp_path, "d.yaml", REQUIRED + "pdd: {jwtSecret: s, backend: {timeout: 10}}")]
        )


@pytest.mark.parametrize(
    ("text", "seconds"), [("500ms", 0.5), ("10s", 10), ("1m30s", 90), ("24h", 86400), ("1.5s", 1.5)]
)
def test_parse_duration(text, seconds):
    assert parse_duration(text).total_seconds() == seconds


@pytest.mark.parametrize("text", ["", "10", "s", "1d", "1s2"])
def test_parse_duration_rejects(text):
    with pytest.raises(ValueError, match="invalid duration"):
        parse_duration(text)


def test_deep_merge_replaces_lists_and_scalars():
    assert deep_merge({"a": {"b": 1, "c": [1]}}, {"a": {"c": [2]}, "d": 1}) == {
        "a": {"b": 1, "c": [2]},
        "d": 1,
    }


def test_llm_defaults_and_official_base_url(tmp_path):
    settings = load_settings([write(tmp_path, "a.yaml", REQUIRED)])
    assert (settings.llm.provider, settings.llm.model) == ("google_genai", "gemini-3.8-flash")
    assert settings.llm.base_url == ""


def test_old_gemini_section_is_reported_as_moved(tmp_path):
    old = write(tmp_path, "old.yaml", REQUIRED + "gemini: {apiKey: k, model: m}\n")
    with pytest.raises(ConfigError) as err:
        load_settings([old])
    assert err.value.errors == [
        "gemini: moved to llm (provider: google_genai, model, apiKey, baseURL, requestTimeout)"
    ]
