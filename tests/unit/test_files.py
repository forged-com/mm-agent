import gzip
import json
from pathlib import Path

import pytest

from mm_agent.drivers.files import files, local_days, pattern, rows
from mm_agent.types import DriverError, Job, Resolved, Source, SourceKind, Table

NOON = 1_792_238_400_000
HOUR = 3_600_000
JOB = Job(start=NOON, end=NOON + HOUR, asset="ETH")
RESOLVED = Resolved(asset="ETH")
GLOB = "orders/BINANCE_SPOT/{asset}USDT/{from}"
ROWS = [
    {"order_id": "1", "side": "buy", "price": "10.5"},
    {"order_id": "2", "side": "sell", "price": "11"},
]
TYPED_ROWS = [
    {"order_id": 1, "side": "buy", "price": 10.5},
    {"order_id": 2, "side": "sell", "price": 11},
]
CSV = "order_id,side,price\n1,buy,10.5\n2,sell,11\n"
JSONL = "".join(json.dumps(row) + "\n" for row in TYPED_ROWS)


def table(query: str) -> Table:
    return Table(query=query, map={})


def source(root: Path, timezone: str = "UTC") -> Source:
    return Source(kind=SourceKind.FILES, dsn=str(root), timezone=timezone)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name.endswith(".gz"):
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    day = tmp_path / "orders" / "BINANCE_SPOT" / "ETHUSDT"
    write(day / "2026-10-17.csv", CSV)
    write(day / "2026-10-17.csv.gz", CSV)
    write(day / "2026-10-17.jsonl", JSONL)
    write(day / "2026-10-17.jsonl.gz", JSONL)
    write(tmp_path / "orders" / "OKX" / "ETHUSDT" / "2026-10-17.csv", "order_id,side,price\n9,buy,1\n")
    return tmp_path


def read(root: Path, glob: str, timezone: str = "UTC") -> list[dict[str, object]]:
    return list(rows(source(root, timezone), table(glob), JOB, RESOLVED))


@pytest.mark.parametrize("suffix", [".csv", ".csv.gz"])
def test_rows_reads_csv_as_strings(root: Path, suffix: str) -> None:
    assert read(root, GLOB + suffix) == ROWS


@pytest.mark.parametrize("suffix", [".jsonl", ".jsonl.gz"])
def test_rows_reads_jsonl_with_json_types(root: Path, suffix: str) -> None:
    assert read(root, GLOB + suffix) == TYPED_ROWS


def test_rows_yields_plain_dicts(root: Path) -> None:
    assert all(type(row) is dict for row in read(root, GLOB + ".csv"))


def test_files_match_sorted_by_path(root: Path) -> None:
    names = [path.name for path in files(source(root), table(GLOB + "*"), JOB, RESOLVED)]
    assert names == ["2026-10-17.csv", "2026-10-17.csv.gz", "2026-10-17.jsonl", "2026-10-17.jsonl.gz"]


def test_rows_concatenate_matched_files_in_order(root: Path) -> None:
    assert read(root, GLOB + "*.csv*") == ROWS + ROWS


def test_files_skip_directories(root: Path) -> None:
    assert files(source(root), table("orders/BINANCE_SPOT/*"), JOB, RESOLVED) == []


def test_rows_with_no_match_is_empty(root: Path) -> None:
    assert read(root, "orders/BINANCE_SPOT/{asset}USDT/2000-01-01.csv") == []


def test_rows_strip_utf8_bom(tmp_path: Path) -> None:
    write(tmp_path / "orders.csv", "﻿" + CSV)
    assert read(tmp_path, "orders.csv") == ROWS


def test_rows_skip_blank_jsonl_lines(tmp_path: Path) -> None:
    write(tmp_path / "orders.jsonl", "\n" + JSONL.replace("\n", "\n\n") + "   \n")
    assert read(tmp_path, "orders.jsonl") == TYPED_ROWS


def test_bad_json_line_names_file_and_line(tmp_path: Path) -> None:
    path = write(tmp_path / "orders.jsonl", JSONL + "{not json\n")
    with pytest.raises(DriverError, match=rf"{path}:3: Expecting property name"):
        read(tmp_path, "orders.jsonl")


def test_json_line_must_be_an_object(tmp_path: Path) -> None:
    path = write(tmp_path / "orders.jsonl", JSONL + "[1, 2]\n")
    with pytest.raises(DriverError, match=rf"{path}:3: expected an object, got list"):
        read(tmp_path, "orders.jsonl")


def test_csv_row_with_extra_values_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "orders.csv", CSV + "3,buy,1,extra\n")
    with pytest.raises(DriverError, match=rf"{path}:4: more values than header columns"):
        read(tmp_path, "orders.csv")


def test_csv_row_with_missing_values_fills_none(tmp_path: Path) -> None:
    write(tmp_path / "orders.csv", "order_id,side,price\n3,buy\n")
    assert read(tmp_path, "orders.csv") == [{"order_id": "3", "side": "buy", "price": None}]


def test_unsupported_file_type_rejected(tmp_path: Path) -> None:
    write(tmp_path / "orders.parquet", "")
    with pytest.raises(DriverError, match=r"orders\.parquet: unsupported file type"):
        read(tmp_path, "orders.parquet")


def test_corrupt_gzip_wrapped(tmp_path: Path) -> None:
    (tmp_path / "orders.csv.gz").write_text(CSV)
    with pytest.raises(DriverError, match=r"orders\.csv\.gz: Not a gzipped file"):
        read(tmp_path, "orders.csv.gz")


def test_undecodable_file_wrapped(tmp_path: Path) -> None:
    (tmp_path / "orders.csv").write_bytes(b"order_id\n\xff\xfe\n")
    with pytest.raises(DriverError, match=r"orders\.csv: 'utf-8' codec can't decode"):
        read(tmp_path, "orders.csv")


def test_root_must_be_a_directory(tmp_path: Path) -> None:
    with pytest.raises(DriverError, match=r"files root .*missing' is not a directory"):
        read(tmp_path / "missing", "*.csv")


def test_unknown_placeholder_rejected(root: Path) -> None:
    with pytest.raises(DriverError, match=r"unknown placeholder \{date\}; known are \['asset', 'from', 'to'\]"):
        read(root, "orders/{date}.csv")


@pytest.mark.parametrize("glob", ["orders/{.csv", "orders/}.csv", "orders/{}.csv", "orders/{0}.csv"])
def test_malformed_placeholder_rejected(root: Path, glob: str) -> None:
    with pytest.raises(DriverError, match=r"^glob "):
        read(root, glob)


def test_pattern_resolves_asset_and_dates(root: Path) -> None:
    resolved = Resolved(asset="XBT")
    assert pattern(source(root), table("{asset}/{from}_{to}.csv"), JOB, resolved) == (
        "XBT/2026-10-17_2026-10-17.csv"
    )


@pytest.mark.parametrize("glob", ["{venue}/a.csv", "{pair}/a.csv"])
def test_pattern_no_longer_knows_venue_or_pair(root: Path, glob: str) -> None:
    with pytest.raises(DriverError, match="unknown placeholder"):
        pattern(source(root), table(glob), JOB, RESOLVED)


def test_pattern_dates_in_source_timezone(root: Path) -> None:
    early = Job(start=NOON - 10 * HOUR, end=NOON - 9 * HOUR, asset=JOB.asset)
    assert (
        pattern(source(root, "America/New_York"), table("{from}_{to}"), early, RESOLVED) == "2026-10-16_2026-10-16"
    )
    assert pattern(source(root), table("{from}_{to}"), early, RESOLVED) == "2026-10-17_2026-10-17"


def test_pattern_daily_window_ends_on_next_day(root: Path) -> None:
    day = Job(start=NOON - 12 * HOUR, end=NOON + 12 * HOUR, asset=JOB.asset)
    assert pattern(source(root), table("{from}_{to}"), day, RESOLVED) == "2026-10-17_2026-10-18"


def test_pattern_leaves_literal_glob_alone(root: Path) -> None:
    assert pattern(source(root), table("orders/**/*.csv"), JOB, RESOLVED) == "orders/**/*.csv"


@pytest.mark.parametrize("text", ["", "order_id,side,price\n"])
def test_csv_without_rows_yields_nothing(tmp_path: Path, text: str) -> None:
    write(tmp_path / "a.csv", text)
    assert read(tmp_path, "a.csv") == []


def test_pattern_hour_window_keeps_from_and_to_on_the_same_day(root: Path) -> None:
    assert pattern(source(root), table("{from}_{to}"), JOB, RESOLVED) == "2026-10-17_2026-10-17"


def test_recursive_glob_crosses_venues(root: Path) -> None:
    assert [row["order_id"] for row in read(root, "orders/**/*.csv")] == ["1", "2", "9"]


def test_utc_day_reads_both_local_dates_it_spans(tmp_path: Path) -> None:
    for day in ("2026-10-16", "2026-10-17", "2026-10-18"):
        write(tmp_path / f"{day}.csv", CSV)
    day = Job(start=NOON - 12 * HOUR, end=NOON + 12 * HOUR, asset=JOB.asset)
    found = files(source(tmp_path, "America/New_York"), table("{from}.csv"), day, RESOLVED)
    assert [path.name for path in found] == ["2026-10-16.csv", "2026-10-17.csv"]
    assert [path.name for path in files(source(tmp_path), table("{from}.csv"), day, RESOLVED)] == [
        "2026-10-17.csv"
    ]


def test_local_days_cut_at_local_midnight(root: Path) -> None:
    day = Job(start=NOON - 12 * HOUR, end=NOON + 12 * HOUR, asset=JOB.asset)
    assert local_days(source(root), day) == [day]
    first, second = local_days(source(root, "America/New_York"), day)
    assert (first.start, first.end, second.start, second.end) == (
        day.start,
        NOON - 8 * HOUR,
        NOON - 8 * HOUR,
        day.end,
    )
