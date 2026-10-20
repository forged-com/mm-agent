from dataclasses import fields
from datetime import UTC, datetime, timedelta
from typing import Any

from mm_agent.constants import EPOCH
from mm_agent.types import ConfigError


def mapping(node: Any, path: str) -> dict[str, Any]:
    if not isinstance(node, dict):
        raise ConfigError(f"{path}: expected a mapping, got {describe(node)}")
    if not all(isinstance(k, str) for k in node):
        raise ConfigError(f"{path}: keys must be strings")
    return node


def string(node: dict[str, Any], key: str, path: str) -> str:
    value = node.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: expected a non-empty string, got {describe(value)}")
    return value


def string_map(node: Any, path: str) -> dict[str, str]:
    result = mapping(node, path)
    for key, value in result.items():
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{path}.{key}: expected a non-empty string, got {describe(value)}")
    return dict(result)


def only_keys(node: dict[str, Any], path: str, allowed: set[str]) -> None:
    if unknown := node.keys() - allowed:
        raise ConfigError(f"{path}: unknown keys {sorted(unknown)}; allowed are {sorted(allowed)}")


def describe(value: Any) -> str:
    if value is None:
        return "nothing"
    if isinstance(value, str) and not value.strip():
        return "an empty string"
    return type(value).__name__


def field_names(cls: type) -> frozenset[str]:
    return frozenset(f.name for f in fields(cls))


def instant(millis: int) -> datetime:
    return EPOCH + timedelta(milliseconds=millis)


def millis(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def now_millis() -> int:
    return millis(datetime.now(UTC))
