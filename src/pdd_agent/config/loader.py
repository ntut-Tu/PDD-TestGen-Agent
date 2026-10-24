"""Layered YAML configuration: embedded defaults, ``--config`` files, strict validation."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import timedelta
from importlib import resources
from pathlib import Path
from typing import Any, get_type_hints

import yaml

from pdd_agent.config.schema import Settings

_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")
_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}


class ConfigError(Exception):
    """Raised with every problem found in the configuration, one per line."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__("invalid configuration:\n" + "\n".join(f"  {e}" for e in self.errors))


def parse_duration(text: str) -> timedelta:
    """Parse a Go-style duration such as ``500ms``, ``1m30s`` or ``24h``."""
    parts = _DURATION_PART.findall(text)
    if not parts or "".join(n + u for n, u in parts) != text:
        raise ValueError(f"invalid duration {text!r}, expected e.g. 500ms, 10s, 1m30s, 24h")
    return timedelta(seconds=sum(float(n) * _UNITS[u] for n, u in parts))


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Merge ``override`` over ``base``: maps merge recursively, everything else replaces."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _key(f: Any) -> str:
    return f.metadata.get("key") or f.name


def _coerce(value: Any, typ: type, path: str, errors: list[str]) -> Any:
    """Convert one YAML scalar to ``typ``; report a path-qualified error on mismatch."""
    if typ is str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        errors.append(f"{path}: must be a string")
    elif typ is bool:
        if isinstance(value, bool):
            return value
        errors.append(f"{path}: must be true or false")
    elif typ is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        errors.append(f"{path}: must be an integer")
    elif typ is timedelta:
        try:
            if isinstance(value, str):
                return parse_duration(value)
            errors.append(f"{path}: must be a duration string such as 10s")
        except ValueError as exc:
            errors.append(f"{path}: {exc}")
    return None


def _build[T](cls: type[T], data: Any, path: str, errors: list[str]) -> T:
    """Build dataclass ``cls`` from a mapping, collecting every problem into ``errors``."""
    if not isinstance(data, Mapping):
        errors.append(f"{path or '<root>'}: must be a mapping")
        data = {}
    hints = get_type_hints(cls)
    known = {_key(f) for f in fields(cls)}  # type: ignore[arg-type]
    prefix = f"{path}." if path else ""
    errors.extend(f"{prefix}{k}: unknown key" for k in data if k not in known)
    values: dict[str, Any] = {}
    for f in fields(cls):  # type: ignore[arg-type]
        child = f"{prefix}{_key(f)}"
        raw = data.get(_key(f))
        typ = hints[f.name]
        if is_dataclass(typ):
            values[f.name] = _build(typ, raw if _key(f) in data else {}, child, errors)
            continue
        if _key(f) not in data:
            values[f.name] = f.default
            raw = f.default
            if f.metadata.get("required") and raw in ("", None):
                errors.append(f"{child}: required")
            continue
        value = _coerce(raw, typ, child, errors)
        values[f.name] = value
        if value is None:
            continue
        if f.metadata.get("required") and value == "":
            errors.append(f"{child}: required")
        elif (check := f.metadata.get("check")) and (message := check(value)):
            errors.append(f"{child}: {message}")
    return cls(**values)


_MOVED_GEMINI = (
    "gemini: moved to llm (provider: google_genai, model, apiKey, baseURL, requestTimeout)"
)


def parse_settings(data: Mapping[str, Any]) -> Settings:
    """Validate a merged configuration mapping and return the typed settings."""
    errors: list[str] = []
    if isinstance(data, Mapping) and "gemini" in data:
        errors.append(_MOVED_GEMINI)
        data = {k: v for k, v in data.items() if k != "gemini"}
    settings = _build(Settings, data, "", errors)
    if errors:
        raise ConfigError(errors)
    return settings


def _read_yaml(text: str, source: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError([f"{source}: invalid YAML: {exc}"]) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError([f"{source}: top level must be a mapping"])
    return data


def load_settings(config_files: Sequence[Path] = ()) -> Settings:
    """Load the embedded defaults, merge ``config_files`` in order and validate the result."""
    defaults = resources.files("pdd_agent.config").joinpath("values.yaml").read_text("utf-8")
    merged = _read_yaml(defaults, "values.yaml")
    for path in config_files:
        try:
            text = path.read_text("utf-8")
        except OSError as exc:
            raise ConfigError([f"{path}: cannot read: {exc.strerror}"]) from exc
        merged = deep_merge(merged, _read_yaml(text, str(path)))
    return parse_settings(merged)
