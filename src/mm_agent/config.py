from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from mm_agent.constants import (
    DEFAULT_TIMEZONE,
    DERIVED_FILL_FIELDS,
    DSN_DIALECTS,
    LOOPBACK_HOSTS,
    MYSQL_SCHEME,
    OPTIONAL_ORDER_FIELDS,
)
from mm_agent.types import Aliases, Config, ConfigError, Control, Fill, Order, Source, SourceKind, Table
from mm_agent.utils import field_names, mapping, only_keys, string, string_map


def parse(text: str) -> Config:
    try:
        root = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"not valid YAML: {e}") from e
    top = mapping(root, "agent.yaml")
    only_keys(top, "agent.yaml", {"mm", "control", "source", "aliases", "orders", "fills"})
    mm = string(top, "mm", "mm")
    control = _control(mapping(top.get("control"), "control"))
    source = _source(mapping(top.get("source"), "source"))
    aliases = _aliases(mapping(top.get("aliases"), "aliases"))
    orders = _table(
        mapping(top.get("orders"), "orders"), "orders", field_names(Order), OPTIONAL_ORDER_FIELDS, source.kind
    )
    fills = _table(
        mapping(top.get("fills"), "fills"),
        "fills",
        field_names(Fill) - DERIVED_FILL_FIELDS,
        frozenset(),
        source.kind,
    )
    return Config(mm=mm, control=control, source=source, aliases=aliases, orders=orders, fills=fills)


def _control(node: dict[str, Any]) -> Control:
    only_keys(node, "control", {"url"})
    url = string(node, "url", "control.url").rstrip("/")
    if not url.startswith(("https://", "http://")):
        raise ConfigError(f"control.url: {url!r} must start with https:// or http://")
    if url.startswith("http://") and urlsplit(url).hostname not in LOOPBACK_HOSTS:
        raise ConfigError(f"control.url: {url!r} must use https:// unless the host is loopback")
    return Control(url=url)


def _source(node: dict[str, Any]) -> Source:
    only_keys(node, "source", {"kind", "dsn", "timezone"})
    name = string(node, "kind", "source.kind")
    try:
        kind = SourceKind(name)
    except ValueError as e:
        raise ConfigError(f"source.kind: {name!r} is not one of {[k.value for k in SourceKind]}") from e
    timezone = string(node, "timezone", "source.timezone") if "timezone" in node else DEFAULT_TIMEZONE
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ConfigError(f"source.timezone: {timezone!r} is not an IANA timezone") from e
    return Source(kind=kind, dsn=_dsn(kind, string(node, "dsn", "source.dsn")), timezone=timezone)


def _dsn(kind: SourceKind, dsn: str) -> str:
    if kind is SourceKind.FILES:
        return dsn
    scheme, separator, _ = dsn.partition("://")
    dialect = scheme.partition("+")[0]
    if not separator or dialect not in DSN_DIALECTS[kind]:
        raise ConfigError(
            f"source.dsn: scheme {scheme!r} does not match kind {kind.value!r}; expected one of {sorted(DSN_DIALECTS[kind])}"
        )
    if scheme == SourceKind.MYSQL.value:
        raise ConfigError(
            f"source.dsn: scheme {scheme!r} needs a driver; use {MYSQL_SCHEME}:// (installed by forgd-mm-agent[mysql])"
        )
    return dsn


def _aliases(node: dict[str, Any]) -> Aliases:
    only_keys(node, "aliases", {"assets", "venues", "pairs"})
    return Aliases(
        assets=string_map(node.get("assets"), "aliases.assets"),
        venues=string_map(node.get("venues", {}), "aliases.venues"),
        pairs=string_map(node.get("pairs", {}), "aliases.pairs"),
    )


def _table(
    node: dict[str, Any], path: str, fields: frozenset[str], optional: frozenset[str], kind: SourceKind
) -> Table:
    only_keys(node, path, {"query", "map"})
    column_map = string_map(node.get("map"), f"{path}.map")
    if missing := fields - optional - column_map.keys():
        raise ConfigError(f"{path}.map: missing {sorted(missing)}")
    if unknown := column_map.keys() - fields:
        raise ConfigError(f"{path}.map: unknown {sorted(unknown)}; schema fields are {sorted(fields)}")
    query = string(node, "query", f"{path}.query")
    if kind is SourceKind.FILES and query.startswith("/"):
        raise ConfigError(f"{path}.query: glob {query!r} must be relative to source.dsn")
    return Table(query=query, map=column_map)
