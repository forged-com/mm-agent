import re

import pytest
import yaml

from mm_agent.config import parse
from mm_agent.types import ConfigError, SourceKind


def dump(doc: dict) -> str:
    return yaml.safe_dump(doc)


def test_parses_valid_config(valid_yaml: str) -> None:
    config = parse(valid_yaml)
    assert config.mm == "acme"
    assert config.control.url == "https://mmcp.example.com"
    assert config.source.kind is SourceKind.SQLITE
    assert config.source.dsn == "sqlite:///oms.db"
    assert config.source.timezone == "UTC"
    assert config.aliases.assets == {"ETH": "ETH"}
    assert config.aliases.venues == {"binance": "BINANCE_SPOT"}
    assert config.aliases.pairs == {"ETH/USDT": "ETHUSDT"}
    assert config.orders.query == "select * from orders"
    assert config.orders.map["placed"] == "created_at"
    assert config.fills.map["ts"] == "exec_time"


def test_invalid_yaml() -> None:
    with pytest.raises(ConfigError, match="not valid YAML"):
        parse("mm: [unclosed")


@pytest.mark.parametrize("text", ["", "- a list", "just a string", "42"])
def test_root_must_be_mapping(text: str) -> None:
    with pytest.raises(ConfigError, match=r"agent\.yaml: expected a mapping"):
        parse(text)


def test_unknown_top_level_key(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["extra"] = 1
    with pytest.raises(ConfigError, match=r"agent.yaml: unknown keys \['extra'\]"):
        parse(dump(doc))


@pytest.mark.parametrize("section", ["mm", "control", "source", "aliases", "orders", "fills"])
def test_missing_section(valid_yaml: str, section: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    del doc[section]
    with pytest.raises(ConfigError, match=f"{section}: expected"):
        parse(dump(doc))


@pytest.mark.parametrize("value", [None, "", "   ", 7, ["x"]])
def test_mm_must_be_non_empty_string(valid_yaml: str, value: object) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["mm"] = value
    with pytest.raises(ConfigError, match="mm: expected a non-empty string"):
        parse(dump(doc))


@pytest.mark.parametrize("url", ["mmcp.example.com", "ftp://mmcp.example.com", "//x"])
def test_control_url_scheme(valid_yaml: str, url: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["control"]["url"] = url
    with pytest.raises(ConfigError, match=r"control\.url: .* must start with https:// or http://"):
        parse(dump(doc))


def test_control_url_strips_trailing_slash(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["control"]["url"] = "http://localhost:8080///"
    assert parse(dump(doc)).control.url == "http://localhost:8080"


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080", "http://[::1]:8080", "http://localhost"])
def test_control_url_plain_http_allowed_for_loopback(valid_yaml: str, url: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["control"]["url"] = url
    assert parse(dump(doc)).control.url == url


@pytest.mark.parametrize("url", ["http://mmcp.forgd.com", "http://10.0.0.5:8080", "http://control.invalid"])
def test_control_url_plain_http_rejected_elsewhere(valid_yaml: str, url: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["control"]["url"] = url
    with pytest.raises(ConfigError, match=r"control\.url: .* must use https:// unless the host is loopback"):
        parse(dump(doc))


def test_control_unknown_key(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["control"]["token"] = "secret"
    with pytest.raises(ConfigError, match=r"control: unknown keys \['token'\]"):
        parse(dump(doc))


def test_source_kind_must_be_known(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["source"]["kind"] = "oracle"
    with pytest.raises(ConfigError, match=r"source\.kind: 'oracle' is not one of"):
        parse(dump(doc))


@pytest.mark.parametrize(
    ("kind", "dsn"),
    [
        ("postgres", "postgresql://readonly@oms-db/oms"),
        ("postgres", "postgresql+psycopg://readonly@oms-db/oms"),
        ("mysql", "mysql+pymysql://readonly@oms-db/oms"),
        ("clickhouse", "clickhousedb://readonly@oms-db/oms"),
        ("sqlite", "sqlite:///oms.db"),
        ("files", "/data/oms/*.csv"),
    ],
)
def test_source_every_kind_accepted(valid_yaml: str, kind: str, dsn: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["source"]["kind"] = kind
    doc["source"]["dsn"] = dsn
    source = parse(dump(doc)).source
    assert source.kind == SourceKind(kind)
    assert source.dsn == dsn


@pytest.mark.parametrize(
    ("kind", "dsn"),
    [
        ("postgres", "postgres://readonly@oms-db/oms"),
        ("postgres", "sqlite:///oms.db"),
        ("mysql", "mariadb://readonly@oms-db/oms"),
        ("clickhouse", "clickhouse://readonly@oms-db/oms"),
        ("clickhouse", "clickhouse+native://readonly@oms-db/oms"),
        ("clickhouse", "http://oms-db:8123"),
        ("sqlite", "oms.db"),
    ],
)
def test_source_dsn_must_match_kind(valid_yaml: str, kind: str, dsn: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["source"]["kind"] = kind
    doc["source"]["dsn"] = dsn
    with pytest.raises(ConfigError, match=rf"source\.dsn: scheme .* does not match kind '{kind}'"):
        parse(dump(doc))


def test_source_bare_mysql_scheme_names_the_driver(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["source"]["kind"] = "mysql"
    doc["source"]["dsn"] = "mysql://readonly@oms-db/oms"
    with pytest.raises(
        ConfigError,
        match=r"^source\.dsn: scheme 'mysql' needs a driver; use mysql\+pymysql:// \(installed by forgd-mm-agent\[mysql\]\)$",
    ):
        parse(dump(doc))


def test_source_timezone_explicit(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["source"]["timezone"] = "Europe/London"
    assert parse(dump(doc)).source.timezone == "Europe/London"


@pytest.mark.parametrize("timezone", ["Mars/Olympus", "UTC+1", "", "/etc/passwd"])
def test_source_timezone_must_be_iana(valid_yaml: str, timezone: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["source"]["timezone"] = timezone
    with pytest.raises(ConfigError, match=r"source\.timezone"):
        parse(dump(doc))


def test_source_unknown_key(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["source"]["password"] = "x"
    with pytest.raises(ConfigError, match=r"source: unknown keys \['password'\]"):
        parse(dump(doc))


def test_venue_and_pair_aliases_default_empty(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["aliases"] = {"assets": {"ETH": "ETH"}}
    config = parse(dump(doc))
    assert config.aliases.venues == {}
    assert config.aliases.pairs == {}


def test_asset_aliases_are_required(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    del doc["aliases"]["assets"]
    with pytest.raises(ConfigError, match=r"aliases\.assets: expected a mapping, got nothing"):
        parse(dump(doc))


def test_aliases_values_must_be_strings(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["aliases"]["pairs"]["ETH/USDT"] = 1
    with pytest.raises(ConfigError, match=r"aliases\.pairs\.ETH/USDT: expected a non-empty string"):
        parse(dump(doc))


def test_aliases_unknown_key(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["aliases"]["symbols"] = {}
    with pytest.raises(ConfigError, match=r"aliases: unknown keys \['symbols'\]"):
        parse(dump(doc))


@pytest.mark.parametrize("table", ["orders", "fills"])
def test_table_map_missing_field(valid_yaml: str, table: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    del doc[table]["map"]["price"]
    with pytest.raises(ConfigError, match=rf"{table}.map: missing \['price'\]"):
        parse(dump(doc))


def test_orders_map_may_include_filled_and_remaining(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["orders"]["map"].update({"filled": "filled_qty", "remaining": "left"})
    assert parse(dump(doc)).orders.map["remaining"] == "left"


def test_fills_map_may_not_name_the_derived_id(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["fills"]["map"]["id"] = "trade_id"
    with pytest.raises(
        ConfigError,
        match=r"fills.map: unknown \['id'\]; schema fields are \['order', 'pair', 'price', 'qty', 'side', 'ts', 'venue'\]",
    ):
        parse(dump(doc))


@pytest.mark.parametrize("table", ["orders", "fills"])
def test_table_map_unknown_field(valid_yaml: str, table: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc[table]["map"]["fee"] = "fee"
    with pytest.raises(ConfigError, match=rf"{table}.map: unknown \['fee'\]"):
        parse(dump(doc))


@pytest.mark.parametrize("table", ["orders", "fills"])
def test_table_map_required(valid_yaml: str, table: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    del doc[table]["map"]
    with pytest.raises(ConfigError, match=f"{table}.map: expected a mapping, got nothing"):
        parse(dump(doc))


@pytest.mark.parametrize("table", ["orders", "fills"])
def test_table_query_required(valid_yaml: str, table: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc[table]["query"] = ""
    with pytest.raises(ConfigError, match=f"{table}.query: expected a non-empty string, got an empty string"):
        parse(dump(doc))


@pytest.mark.parametrize("table", ["orders", "fills"])
def test_files_glob_must_be_relative(valid_yaml: str, table: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["source"] = {"kind": "files", "dsn": "/exports"}
    doc[table]["query"] = "/exports/orders/*.csv"
    with pytest.raises(ConfigError, match=rf"{table}\.query: glob '/exports/orders/\*\.csv' must be relative"):
        parse(dump(doc))


def test_sql_query_may_start_with_slash(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["orders"]["query"] = "/* hint */ select 1"
    assert parse(dump(doc)).orders.query == "/* hint */ select 1"


def test_table_unknown_key(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["orders"]["limit"] = 10
    with pytest.raises(ConfigError, match=r"orders: unknown keys \['limit'\]"):
        parse(dump(doc))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("control.url", 7),
        ("source.kind", 1),
        ("source.dsn", None),
        ("source.timezone", 3),
        ("orders.query", ""),
        ("fills.query", ["x"]),
    ],
)
def test_scalar_settings_must_be_non_empty_strings(valid_yaml: str, path: str, value: object) -> None:
    doc = yaml.safe_load(valid_yaml)
    section, key = path.split(".")
    doc[section][key] = value
    with pytest.raises(ConfigError, match=rf"{re.escape(path)}: expected a non-empty string"):
        parse(dump(doc))


def test_top_level_keys_must_be_strings() -> None:
    with pytest.raises(ConfigError, match=r"agent\.yaml: keys must be strings"):
        parse("1: x\n")


def test_alias_keys_must_be_strings(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["aliases"]["pairs"] = {1: "x"}
    with pytest.raises(ConfigError, match=r"aliases\.pairs: keys must be strings"):
        parse(dump(doc))


def test_alias_values_must_not_be_blank(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["aliases"]["venues"]["binance"] = "  "
    with pytest.raises(
        ConfigError, match=r"aliases\.venues\.binance: expected a non-empty string, got an empty string"
    ):
        parse(dump(doc))


def test_alias_sections_must_be_mappings(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["aliases"]["venues"] = ["binance"]
    with pytest.raises(ConfigError, match=r"aliases\.venues: expected a mapping, got list"):
        parse(dump(doc))


def test_map_values_must_be_non_empty_strings(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["orders"]["map"]["price"] = ""
    with pytest.raises(ConfigError, match=r"orders\.map\.price: expected a non-empty string"):
        parse(dump(doc))


def test_map_must_be_a_mapping(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["fills"]["map"] = ["order_id"]
    with pytest.raises(ConfigError, match=r"fills\.map: expected a mapping, got list"):
        parse(dump(doc))


def test_orders_map_may_include_only_one_of_filled_and_remaining(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["orders"]["map"]["remaining"] = "left"
    assert parse(dump(doc)).orders.map.get("filled") is None


@pytest.mark.parametrize(
    ("kind", "dsn"),
    [("postgres", "POSTGRESQL://x/y"), ("postgres", "postgresql:/x"), ("sqlite", "SQLITE:///x")],
)
def test_dsn_scheme_is_case_sensitive_and_needs_a_separator(valid_yaml: str, kind: str, dsn: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["source"]["kind"] = kind
    doc["source"]["dsn"] = dsn
    with pytest.raises(ConfigError, match=r"does not match kind"):
        parse(dump(doc))


def test_control_url_scheme_is_case_sensitive(valid_yaml: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["control"]["url"] = "HTTPS://mmcp.example.com"
    with pytest.raises(ConfigError, match=r"must start with https"):
        parse(dump(doc))


@pytest.mark.parametrize(
    ("kind", "dsn"),
    [
        ("sqlite", "sqlite+pysqlite:///oms.db"),
        ("postgres", "postgresql+asyncpg://x/y"),
        ("clickhouse", "clickhousedb+connect://x/y"),
    ],
)
def test_any_driver_suffix_is_accepted_at_config_time(valid_yaml: str, kind: str, dsn: str) -> None:
    doc = yaml.safe_load(valid_yaml)
    doc["source"]["kind"] = kind
    doc["source"]["dsn"] = dsn
    assert parse(dump(doc)).source.dsn == dsn
