import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from mm_agent.constants import FETCH_ROWS
from mm_agent.drivers.sql import params, rows
from mm_agent.types import DriverError, Job, Resolved, Source, SourceKind, Table

NOON = 1_792_238_400_000
HOUR = 3_600_000
JOB = Job(start=NOON, end=NOON + HOUR, asset="ETH")
RESOLVED = Resolved(asset="ETH")
ORDERS = [
    (1, "BINANCE_SPOT", "ETHUSDT", "2026-10-17 11:59:59", "2026-10-17 11:59:59+00:00"),
    (2, "BINANCE_SPOT", "ETHUSDT", "2026-10-17 12:00:00", "2026-10-17 12:00:00+00:00"),
    (3, "BINANCE_SPOT", "ETHUSDT", "2026-10-17 12:59:59", "2026-10-17 12:59:59+00:00"),
    (4, "BINANCE_SPOT", "ETHUSDT", "2026-10-17 13:00:00", "2026-10-17 13:00:00+00:00"),
    (5, "OKX", "ETHUSDT", "2026-10-17 12:30:00", "2026-10-17 12:30:00+00:00"),
    (6, "BINANCE_SPOT", "BTCUSDT", "2026-10-17 12:30:00", "2026-10-17 12:30:00+00:00"),
]
WINDOW = "where symbol like :asset || '%' and {column} >= :{start} and {column} < :{end} order by order_id"
NAIVE_QUERY = "select order_id, created_at from orders " + WINDOW.format(
    column="created_at", start="from", end="to"
)
UTC_QUERY = "select order_id from orders " + WINDOW.format(column="created_utc", start="from_utc", end="to_utc")


def table(query: str) -> Table:
    return Table(query=query, map={})


@pytest.fixture
def source(tmp_path: Path) -> Source:
    db = tmp_path / "oms.db"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "create table orders (order_id integer, venue text, symbol text, created_at text, created_utc text)"
        )
        connection.executemany("insert into orders values (?, ?, ?, ?, ?)", ORDERS)
        connection.execute("create table fills (fill_id integer)")
        connection.executemany("insert into fills values (?)", [(i,) for i in range(FETCH_ROWS * 2 + 1)])
    return Source(kind=SourceKind.SQLITE, dsn=f"sqlite:///{db}", timezone="UTC")


def test_rows_binds_window_and_asset_across_every_venue(source: Source) -> None:
    result = list(rows(source, table(NAIVE_QUERY), JOB, RESOLVED))
    assert result == [
        {"order_id": 2, "created_at": "2026-10-17 12:00:00"},
        {"order_id": 3, "created_at": "2026-10-17 12:59:59"},
        {"order_id": 5, "created_at": "2026-10-17 12:30:00"},
    ]


def test_rows_binds_utc_window(source: Source) -> None:
    assert [r["order_id"] for r in rows(source, table(UTC_QUERY), JOB, RESOLVED)] == [2, 3, 5]


def test_rows_in_source_timezone_shift_the_window(source: Source) -> None:
    berlin = Source(kind=source.kind, dsn=source.dsn, timezone="Europe/Berlin")
    assert [r["order_id"] for r in rows(berlin, table(NAIVE_QUERY), JOB, RESOLVED)] == []
    shifted = Job(start=NOON - 2 * HOUR, end=NOON - HOUR, asset=JOB.asset)
    assert [r["order_id"] for r in rows(berlin, table(NAIVE_QUERY), shifted, RESOLVED)] == [2, 3, 5]


def test_rows_streams_past_fetch_size(source: Source) -> None:
    assert sum(1 for _ in rows(source, table("select fill_id from fills"), JOB, RESOLVED)) == FETCH_ROWS * 2 + 1


def test_rows_yields_plain_dicts(source: Source) -> None:
    row = next(rows(source, table("select order_id, venue from orders"), JOB, RESOLVED))
    assert type(row) is dict
    assert row == {"order_id": 1, "venue": "BINANCE_SPOT"}


def test_rows_wraps_query_errors(source: Source) -> None:
    with pytest.raises(DriverError, match=r"sqlite query failed: .*no such table: nope"):
        list(rows(source, table("select * from nope"), JOB, RESOLVED))


def test_rows_wraps_connection_errors(tmp_path: Path) -> None:
    source = Source(kind=SourceKind.SQLITE, dsn=f"sqlite:///{tmp_path}/missing/oms.db", timezone="UTC")
    with pytest.raises(DriverError, match="sqlite query failed"):
        list(rows(source, table("select 1"), JOB, RESOLVED))


def test_rows_wraps_engine_errors() -> None:
    source = Source(kind=SourceKind.POSTGRES, dsn="postgresql+nosuchdriver://oms-db/oms", timezone="UTC")
    with pytest.raises(DriverError, match="postgres engine failed"):
        list(rows(source, table("select 1"), JOB, RESOLVED))


def test_rows_names_the_extra_when_the_dbapi_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(dsn: str) -> None:
        raise ModuleNotFoundError("No module named 'psycopg'")

    monkeypatch.setattr("mm_agent.drivers.sql.create_engine", missing)
    source = Source(kind=SourceKind.POSTGRES, dsn="postgresql://oms-db/oms", timezone="UTC")
    with pytest.raises(
        DriverError,
        match=r"^postgres engine failed: No module named 'psycopg'; install forgd-mm-agent\[postgres\]$",
    ):
        list(rows(source, table("select 1"), JOB, RESOLVED))


def test_params_utc() -> None:
    source = Source(kind=SourceKind.SQLITE, dsn="sqlite:///oms.db", timezone="UTC")
    assert params(source, JOB, RESOLVED) == {
        "from": datetime.fromisoformat("2026-10-17T12:00:00"),
        "to": datetime.fromisoformat("2026-10-17T13:00:00"),
        "from_utc": datetime.fromisoformat("2026-10-17T12:00:00+00:00"),
        "to_utc": datetime.fromisoformat("2026-10-17T13:00:00+00:00"),
        "asset": "ETH",
    }


def test_params_naive_in_source_timezone() -> None:
    source = Source(kind=SourceKind.SQLITE, dsn="sqlite:///oms.db", timezone="America/New_York")
    bound = params(source, JOB, RESOLVED)
    assert bound["from"] == datetime.fromisoformat("2026-10-17T08:00:00")
    assert bound["to"] == datetime.fromisoformat("2026-10-17T09:00:00")
    assert bound["from_utc"] == datetime.fromisoformat("2026-10-17T12:00:00+00:00")
    assert bound["from_utc"].tzinfo is not None


def test_params_keep_milliseconds() -> None:
    source = Source(kind=SourceKind.SQLITE, dsn="sqlite:///oms.db", timezone="UTC")
    job = Job(start=NOON + 123, end=NOON + 456, asset=JOB.asset)
    bound = params(source, job, RESOLVED)
    assert bound["from"] == datetime.fromisoformat("2026-10-17T12:00:00.123")
    assert bound["to_utc"] == datetime.fromisoformat("2026-10-17T12:00:00.456+00:00")
