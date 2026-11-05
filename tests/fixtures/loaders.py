import csv
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text

from tests.fixtures import golden

ORDER_COLUMNS = (
    "order_id",
    "venue",
    "symbol",
    "base",
    "side",
    "price",
    "orig_qty",
    "created_at",
    "closed_at",
    "filled_qty",
    "remaining_qty",
)
FILL_COLUMNS = ("order_id", "venue", "symbol", "base", "exec_time", "px", "qty", "side")
POSTGRES_DDL = (
    "drop table if exists orders",
    "drop table if exists fills",
    """
    create table orders (
      order_id text primary key, venue text not null, symbol text not null, base text not null, side text not null,
      price numeric(18,2) not null, orig_qty numeric(18,4) not null,
      created_at timestamptz not null, closed_at timestamptz,
      filled_qty numeric(18,4), remaining_qty numeric(18,4))
    """,
    """
    create table fills (
      order_id text not null, venue text not null, symbol text not null, base text not null,
      exec_time timestamptz not null,
      px numeric(18,2) not null, qty numeric(18,4) not null, side text not null)
    """,
)
MYSQL_DDL = (
    "drop table if exists orders",
    "drop table if exists fills",
    """
    create table orders (
      order_id varchar(32) primary key, venue varchar(32) not null, symbol varchar(32) not null, base varchar(32) not null,
      side varchar(8) not null, price decimal(18,2) not null, orig_qty decimal(18,4) not null,
      created_at datetime(3) not null, closed_at datetime(3) null,
      filled_qty decimal(18,4) null, remaining_qty decimal(18,4) null)
    """,
    """
    create table fills (
      order_id varchar(32) not null, venue varchar(32) not null, symbol varchar(32) not null, base varchar(32) not null,
      exec_time datetime(3) not null, px decimal(18,2) not null, qty decimal(18,4) not null,
      side varchar(8) not null)
    """,
)
CLICKHOUSE_DDL = (
    "drop table if exists orders",
    "drop table if exists fills",
    """
    create table orders (
      order_id String, venue String, symbol String, base String, side Int8,
      price Decimal(18,2), orig_qty Decimal(18,4),
      created_at DateTime64(3, 'UTC'), closed_at Nullable(DateTime64(3, 'UTC')),
      filled_qty Nullable(Decimal(18,4)), remaining_qty Nullable(Decimal(18,4)))
    engine = MergeTree order by (venue, symbol, order_id)
    """,
    """
    create table fills (
      order_id String, venue String, symbol String, base String, exec_time DateTime64(3, 'UTC'),
      px Decimal(18,2), qty Decimal(18,4), side Int8)
    engine = MergeTree order by (venue, symbol, exec_time, order_id)
    """,
)
SQLITE_DDL = (
    "drop table if exists orders",
    "drop table if exists fills",
    """
    create table orders (
      order_id text, venue text, symbol text, base text, side text, price text, orig_qty text,
      created_at text, closed_at text, filled_qty text, remaining_qty text)
    """,
    "create table fills (order_id text, venue text, symbol text, base text, exec_time text, px text, qty text, side text)",
)
HUMMINGBOT_DDL = (
    'drop table if exists "Order"',
    "drop table if exists OrderStatus",
    "drop table if exists TradeFill",
    """
    create table "Order" (
      id text not null, config_file_path text not null, strategy text not null, market text not null,
      symbol text not null, base_asset text not null, quote_asset text not null,
      creation_timestamp bigint not null, order_type text not null, amount bigint not null,
      leverage integer not null, price bigint not null, last_status text not null,
      last_update_timestamp bigint not null, exchange_order_id text, position text,
      primary key (id))
    """,
    """
    create table OrderStatus (
      id integer not null, order_id text not null, timestamp bigint not null, status text not null,
      primary key (id), foreign key (order_id) references "Order" (id))
    """,
    """
    create table TradeFill (
      config_file_path text not null, strategy text not null, market text not null, symbol text not null,
      base_asset text not null, quote_asset text not null, timestamp bigint not null, order_id text not null,
      trade_type text not null, order_type text not null, price bigint not null, amount bigint not null,
      leverage integer not null, trade_fee json not null, trade_fee_in_quote bigint,
      exchange_trade_id text not null, position text,
      primary key (market, order_id, exchange_trade_id), foreign key (order_id) references "Order" (id))
    """,
    'create index o_config_timestamp_index on "Order" (config_file_path, creation_timestamp)',
    'create index o_market_trading_pair_timestamp_index on "Order" (market, symbol, creation_timestamp)',
    'create index o_market_base_asset_timestamp_index on "Order" (market, base_asset, creation_timestamp)',
    'create index o_market_quote_asset_timestamp_index on "Order" (market, quote_asset, creation_timestamp)',
    "create index os_order_id_timestamp_index on OrderStatus (order_id, timestamp)",
    "create index tf_config_timestamp_index on TradeFill (config_file_path, timestamp)",
    "create index tf_market_trading_pair_timestamp_index on TradeFill (market, symbol, timestamp)",
    "create index tf_market_base_asset_timestamp_index on TradeFill (market, base_asset, timestamp)",
    "create index tf_market_quote_asset_timestamp_index on TradeFill (market, quote_asset, timestamp)",
)
HUMMINGBOT_SCALE = 10**6
HUMMINGBOT_CONFIG = "conf_pure_mm_golden.yml"
HUMMINGBOT_STRATEGY = "pure_market_making"
HUMMINGBOT_ORDER_TYPE = "LIMIT"
HUMMINGBOT_POSITION = "NIL"
HUMMINGBOT_FEE_RATE = Decimal("0.001")
HUMMINGBOT_CREATED = {"buy": "BuyOrderCreated", "sell": "SellOrderCreated"}
HUMMINGBOT_COMPLETED = {"buy": "BuyOrderCompleted", "sell": "SellOrderCompleted"}
HUMMINGBOT_FILLED = "OrderFilled"
HUMMINGBOT_CANCELLED = "OrderCancelled"
SIDES = {
    "postgres": {"buy": "buy", "sell": "sell"},
    "mysql": {"buy": "BUY", "sell": "SELL"},
    "clickhouse": {"buy": 1, "sell": -1},
    "sqlite": {"buy": "b", "sell": "s"},
    "files": {"buy": "bid", "sell": "ask"},
}


def postgres(dsn: str) -> None:
    _load(dsn, POSTGRES_DDL, _tables(_aware, SIDES["postgres"]))


def mysql(dsn: str) -> None:
    _load(dsn, MYSQL_DDL, _tables(_local, SIDES["mysql"]))


def clickhouse(dsn: str) -> None:
    engine = create_engine(dsn)
    try:
        with engine.begin() as connection:
            for statement in CLICKHOUSE_DDL:
                connection.execute(text(statement))
            for table, columns, rows in _tables(_clickhouse_timestamp, SIDES["clickhouse"]):
                values = ", ".join(_clickhouse_tuple(row, columns) for row in rows)
                connection.execute(text(f"insert into {table} ({', '.join(columns)}) values {values}"))
    finally:
        engine.dispose()


def sqlite(path: Path) -> str:
    dsn = f"sqlite:///{path}"
    _load(dsn, SQLITE_DDL, _tables(_naive_text, SIDES["sqlite"]))
    return dsn


def hummingbot(path: Path) -> str:
    dsn = f"sqlite:///{path}"
    _load(dsn, HUMMINGBOT_DDL, _hummingbot_tables())
    return dsn


def files(root: Path) -> None:
    for table, columns, rows in _tables(_iso_text, SIDES["files"]):
        for (base, venue, symbol), group in _grouped(rows).items():
            path = root / table / base / venue / f"{symbol}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                for row in group:
                    writer.writerow({k: "" if v is None else v for k, v in row.items()})


def _load(
    dsn: str, ddl: tuple[str, ...], tables: Iterator[tuple[str, tuple[str, ...], list[dict[str, Any]]]]
) -> None:
    engine = create_engine(dsn)
    try:
        with engine.begin() as connection:
            for statement in ddl:
                connection.execute(text(statement))
            for table, columns, rows in tables:
                placeholders = ", ".join(f":{column}" for column in columns)
                connection.execute(
                    text(f"insert into {table} ({', '.join(columns)}) values ({placeholders})"), rows
                )
    finally:
        engine.dispose()


def _tables(
    stamp: Callable[[int], Any], sides: dict[str, Any]
) -> Iterator[tuple[str, tuple[str, ...], list[dict[str, Any]]]]:
    yield "orders", ORDER_COLUMNS, [_order_row(row, stamp, sides) for row in golden.orders()]
    yield "fills", FILL_COLUMNS, [_fill_row(row, stamp, sides) for row in golden.fills()]


def _order_row(row: dict[str, Any], stamp: Callable[[int], Any], sides: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": row["id"],
        "venue": golden.VENUES[row["venue"]],
        "symbol": golden.PAIRS[row["pair"]],
        "base": golden.ASSETS[golden.asset_of(row)],
        "side": sides[row["side"]],
        "price": row["price"],
        "orig_qty": row["qty"],
        "created_at": stamp(row["placed"]),
        "closed_at": None if row["ended"] is None else stamp(row["ended"]),
        "filled_qty": row["filled"],
        "remaining_qty": row["remaining"],
    }


def _fill_row(row: dict[str, Any], stamp: Callable[[int], Any], sides: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": row["order"],
        "venue": golden.VENUES[row["venue"]],
        "symbol": golden.PAIRS[row["pair"]],
        "base": golden.ASSETS[golden.asset_of(row)],
        "exec_time": stamp(row["ts"]),
        "px": row["price"],
        "qty": row["qty"],
        "side": sides[row["side"]],
    }


def _hummingbot_tables() -> Iterator[tuple[str, tuple[str, ...], list[dict[str, Any]]]]:
    fills = _grouped_fills()
    orders = [_hummingbot_order(row, fills.get(row["id"], [])) for row in golden.orders()]
    statuses = [
        status for row in golden.orders() for status in _hummingbot_statuses(row, fills.get(row["id"], []))
    ]
    trades = [_hummingbot_fill(row, number) for number, row in enumerate(golden.fills(), start=1)]
    yield '"Order"', tuple(orders[0]), orders
    yield "OrderStatus", tuple(statuses[0]), statuses
    yield "TradeFill", tuple(trades[0]), trades


def _grouped_fills() -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(golden.fills(), key=lambda row: row["ts"]):
        groups.setdefault(row["order"], []).append(row)
    return groups


def _hummingbot_order(row: dict[str, Any], fills: list[dict[str, Any]]) -> dict[str, Any]:
    symbol = _hummingbot_symbol(row["pair"])
    base_asset, quote_asset = symbol.split("-")
    return {
        "id": row["id"],
        "config_file_path": HUMMINGBOT_CONFIG,
        "strategy": HUMMINGBOT_STRATEGY,
        "market": row["venue"],
        "symbol": symbol,
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "creation_timestamp": row["placed"],
        "order_type": HUMMINGBOT_ORDER_TYPE,
        "amount": _scaled(row["qty"]),
        "leverage": 1,
        "price": _scaled(row["price"]),
        "last_status": _hummingbot_last_status(row, fills),
        "last_update_timestamp": _hummingbot_last_update(row, fills),
        "exchange_order_id": f"x{row['id']}",
        "position": HUMMINGBOT_POSITION,
    }


def _hummingbot_statuses(row: dict[str, Any], fills: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    yield {"order_id": row["id"], "timestamp": row["placed"], "status": HUMMINGBOT_CREATED[row["side"]]}
    for fill in fills:
        yield {"order_id": row["id"], "timestamp": fill["ts"], "status": HUMMINGBOT_FILLED}
    if row["ended"] is not None:
        yield {"order_id": row["id"], "timestamp": row["ended"], "status": _hummingbot_terminal(row)}


def _hummingbot_last_status(row: dict[str, Any], fills: list[dict[str, Any]]) -> str:
    if row["ended"] is not None:
        return _hummingbot_terminal(row)
    return HUMMINGBOT_FILLED if fills else HUMMINGBOT_CREATED[row["side"]]


def _hummingbot_last_update(row: dict[str, Any], fills: list[dict[str, Any]]) -> int:
    if row["ended"] is not None:
        return int(row["ended"])
    return int(fills[-1]["ts"]) if fills else int(row["placed"])


def _hummingbot_terminal(row: dict[str, Any]) -> str:
    if row["filled"] is not None and Decimal(row["filled"]) == Decimal(row["qty"]):
        return HUMMINGBOT_COMPLETED[row["side"]]
    return HUMMINGBOT_CANCELLED


def _hummingbot_fill(row: dict[str, Any], number: int) -> dict[str, Any]:
    symbol = _hummingbot_symbol(row["pair"])
    base_asset, quote_asset = symbol.split("-")
    fee = Decimal(row["price"]) * Decimal(row["qty"]) * HUMMINGBOT_FEE_RATE
    return {
        "config_file_path": HUMMINGBOT_CONFIG,
        "strategy": HUMMINGBOT_STRATEGY,
        "market": row["venue"],
        "symbol": symbol,
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "timestamp": row["ts"],
        "order_id": row["order"],
        "trade_type": row["side"].upper(),
        "order_type": HUMMINGBOT_ORDER_TYPE,
        "price": _scaled(row["price"]),
        "amount": _scaled(row["qty"]),
        "leverage": 1,
        "trade_fee": json.dumps(
            {
                "fee_type": "AddedToCost",
                "percent": str(HUMMINGBOT_FEE_RATE),
                "percent_token": quote_asset,
                "flat_fees": [],
            }
        ),
        "trade_fee_in_quote": _scaled(fee),
        "exchange_trade_id": f"t{number}",
        "position": HUMMINGBOT_POSITION,
    }


def _hummingbot_symbol(pair: str) -> str:
    return pair.replace("/", "-")


def _scaled(value: str | Decimal) -> int:
    return int(Decimal(value) * HUMMINGBOT_SCALE)


def _grouped(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["base"], row["venue"], row["symbol"]), []).append(row)
    return groups


def _aware(millis: int) -> datetime:
    return datetime.fromtimestamp(millis / 1000, tz=UTC)


def _local(millis: int) -> datetime:
    return _aware(millis).astimezone(ZoneInfo(golden.LOCAL_TIMEZONE)).replace(tzinfo=None)


def _naive_text(millis: int) -> str:
    return _aware(millis).replace(tzinfo=None).isoformat(" ")


def _iso_text(millis: int) -> str:
    return _aware(millis).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clickhouse_timestamp(millis: int) -> str:
    return _aware(millis).replace(tzinfo=None).isoformat(" ", timespec="milliseconds")


def _clickhouse_tuple(row: dict[str, Any], columns: tuple[str, ...]) -> str:
    return "(" + ", ".join(_clickhouse_literal(column, row[column]) for column in columns) + ")"


def _clickhouse_literal(column: str, value: Any) -> str:
    if value is None:
        return "NULL"
    if column in ("created_at", "closed_at", "exec_time"):
        return f"toDateTime64('{value}', 3, 'UTC')"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"
