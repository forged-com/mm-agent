import sqlite3
from pathlib import Path
from typing import Any

import pytest

from mm_agent.__main__ import main
from mm_agent.conform import windows
from mm_agent.constants import CHECK_STABLE, TOKEN_ENV
from tests.unit.test_main import HOUR, NOON, YAML

ORDERS = [
    ("o1", "buy", "1850.50", 0.25, "2026-10-17 12:00:00", None),
    ("o2", "sell", "1851", 1, "2026-10-17 12:30:00", "2026-10-17 12:45:00"),
    ("o3", "buy", "1849", 2, "2026-10-17 13:30:00", "2026-10-17 13:50:00"),
]
FILLS = [
    ("o1", "2026-10-17 12:10:00", 1850.5, "0.25", "buy"),
    ("o2", "2026-10-17 12:40:00", 1851, "1", "sell"),
    ("o3", "2026-10-17 13:40:00", 1849, "2", "buy"),
]
ORDERS_QUERY = "select * from orders where created_at < :to and (closed_at is null or closed_at >= :from)"
FILLS_QUERY = "select * from fills where exec_time >= :from and exec_time < :to"
WINDOWED_YAML = YAML.replace("query: select * from orders", f"query: {ORDERS_QUERY}").replace(
    "query: select * from fills", f"query: {FILLS_QUERY}"
)
TWO_HOURS = ("--from", "2026-10-17T12:00Z", "--to", "2026-10-17T14:00Z")
ONE_HOUR = ("--from", "2026-10-17T12:00Z", "--to", "2026-10-17T13:00Z")


def database(path: Path, orders: list[tuple[Any, ...]] = ORDERS, fills: list[tuple[Any, ...]] = FILLS) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute("create table orders (order_id, side, price, orig_qty, created_at, closed_at)")
        connection.executemany("insert into orders values (?, ?, ?, ?, ?, ?)", orders)
        connection.execute("create table fills (order_id, exec_time, px, qty, side)")
        connection.executemany("insert into fills values (?, ?, ?, ?, ?)", fills)
    return path


def conform(
    tmp_path: Path, yaml: str, *flags: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[int, list[str]]:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    path = tmp_path / "agent.yaml"
    path.write_text(yaml)
    code = main([str(path), "--conform", *flags])
    return code, capsys.readouterr().out.splitlines()


def verdicts(lines: list[str]) -> list[str]:
    return [line.strip() for line in lines if line.startswith(("    PASS ", "    FAIL "))]


def test_windows_adds_the_first_hour_to_longer_windows() -> None:
    assert windows(NOON, NOON + HOUR) == [(NOON, NOON + HOUR)]
    assert windows(NOON, NOON + 24 * HOUR) == [(NOON, NOON + 24 * HOUR), (NOON, NOON + HOUR)]
    with pytest.raises(ValueError, match="at least an hour"):
        windows(NOON, NOON + HOUR - 1)


def test_clean_window_passes_every_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    yaml = WINDOWED_YAML.format(url="https://control.invalid", db=database(tmp_path / "oms.db"))
    code, lines = conform(tmp_path, yaml, *TWO_HOURS, monkeypatch=monkeypatch, capsys=capsys)
    assert code == 0
    headers = [line for line in lines if line.startswith("forgd-mm-agent ")]
    assert len(headers) == 2 and all(line.endswith(" conformance gate for acme") for line in headers)
    assert f"window [2026-10-17T12:00:00Z, 2026-10-17T14:00:00Z) = [{NOON}, {NOON + 2 * HOUR})" in lines
    assert f"window [2026-10-17T12:00:00Z, 2026-10-17T13:00:00Z) = [{NOON}, {NOON + HOUR})" in lines
    assert lines.count("  conformance:") == 2
    checks = verdicts(lines)
    assert len(checks) == 14 and all(line.startswith("PASS ") for line in checks)
    assert "PASS query returns every mapped column (verified on 6 raw row(s))" in checks
    assert "PASS map covers every schema field (optional ['filled', 'remaining'] not mapped)" in checks
    assert "PASS timestamps fall inside the window" in checks
    assert lines[-1] == "conformance PASS: 14 check(s) passed over 2 job run(s) in 2 window(s)"
    assert sum('"kind": "run"' in line for line in lines) == 2


def test_unwindowed_queries_fail_the_window_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    yaml = YAML.format(url="https://control.invalid", db=database(tmp_path / "oms.db"))
    code, lines = conform(tmp_path, yaml, *ONE_HOUR, monkeypatch=monkeypatch, capsys=capsys)
    assert code == 1
    checks = verdicts(lines)
    assert len(checks) == 7
    assert [line for line in checks if line.startswith("FAIL ")] == [
        (
            "FAIL timestamps fall inside the window: 2 row(s) outside the window,"
            f" first order 'o3' placed={NOON + 90 * 60_000} ended={NOON + 110 * 60_000}"
        )
    ]
    assert lines[-1] == "conformance FAIL: 1 of 7 check(s) failed"


def test_sentinels_and_orphans_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    orders = [*ORDERS[:2], ("o4", "buy", "1", 1, "2026-10-17 12:20:00", "1970-01-01 00:00:00")]
    fills = [*FILLS[:2], ("o9", "2026-10-17 12:50:00", 1, "1", "buy")]
    yaml = YAML.format(url="https://control.invalid", db=database(tmp_path / "oms.db", orders, fills))
    code, lines = conform(tmp_path, yaml, *ONE_HOUR, monkeypatch=monkeypatch, capsys=capsys)
    assert code == 1
    assert [line for line in verdicts(lines) if line.startswith("FAIL ")] == [
        (
            "FAIL timestamps fall inside the window: 1 row(s) outside the window,"
            f" first order 'o4' placed={NOON + 20 * 60_000} ended=0"
        ),
        (
            "FAIL open orders have null ended: 1 row(s) with ended outside [placed, now],"
            f" first order 'o4' placed={NOON + 20 * 60_000} ended=0"
        ),
        (
            "FAIL every fill references an order in the pull: 1 row(s) referencing no order in the pull,"
            f" first fill of order 'o9' ts={NOON + 50 * 60_000}"
        ),
    ]
    assert lines[-1] == "conformance FAIL: 3 of 7 check(s) failed"


def test_empty_fills_fail_the_count_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    yaml = WINDOWED_YAML.replace(FILLS_QUERY, "select * from fills where 0")
    yaml = yaml.format(url="https://control.invalid", db=database(tmp_path / "oms.db"))
    code, lines = conform(tmp_path, yaml, *ONE_HOUR, monkeypatch=monkeypatch, capsys=capsys)
    assert code == 1
    assert [line for line in verdicts(lines) if line.startswith("FAIL ")] == [
        "FAIL orders and fills are non-empty: fills.mapped: 0 row(s)"
    ]


def test_unstable_query_fails_the_hash_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    yaml = WINDOWED_YAML.replace("select * from fills", "select *, random() as noise from fills")
    yaml = yaml.format(url="https://control.invalid", db=database(tmp_path / "oms.db"))
    code, lines = conform(tmp_path, yaml, *ONE_HOUR, monkeypatch=monkeypatch, capsys=capsys)
    assert code == 1
    failures = [line for line in verdicts(lines) if line.startswith("FAIL ")]
    assert len(failures) == 1
    assert failures[0].startswith(
        f"FAIL {CHECK_STABLE}: fills.raw changed between runs; fills.raw: 2 row(s) sha256 "
    )
    assert " then 2 row(s) sha256 " in failures[0]


def test_failed_job_reports_only_that_it_did_not_land(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    orders = [("o1", "hold", "1", 1, "2026-10-17 12:00:00", None)]
    yaml = WINDOWED_YAML.format(url="https://control.invalid", db=database(tmp_path / "oms.db", orders))
    code, lines = conform(tmp_path, yaml, *ONE_HOUR, monkeypatch=monkeypatch, capsys=capsys)
    assert code == 1
    assert verdicts(lines) == ["FAIL job lands: orders.side (side): unknown side 'hold'"]
    assert lines[-1] == "conformance FAIL: 1 of 1 check(s) failed"


def test_no_jobs_is_a_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    yaml = WINDOWED_YAML.replace("pairs: {{ETH/USDT: ETHUSDT}}", "pairs: {{}}")
    yaml = yaml.format(url="https://control.invalid", db=database(tmp_path / "oms.db"))
    code, lines = conform(tmp_path, yaml, *ONE_HOUR, monkeypatch=monkeypatch, capsys=capsys)
    assert code == 1
    assert verdicts(lines) == []
    assert lines[-1] == "conformance FAIL: no jobs; configure at least one pair and one venue"


def test_short_window_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(
            [str(tmp_path / "agent.yaml"), "--conform", "--from", "2026-10-17T12:00Z", "--to", "2026-10-17T12:30Z"]
        )
    assert exit_info.value.code == 2
    assert "--conform needs a window of at least an hour" in capsys.readouterr().err


def test_conform_excludes_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([str(tmp_path / "agent.yaml"), "--conform", "--dry-run", "--day", "2026-10-17"])
    assert exit_info.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
