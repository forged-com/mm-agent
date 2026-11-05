import gzip
import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from mm_agent import __version__
from mm_agent.__main__ import fetch, land, load, main, process, run
from mm_agent.config import parse
from mm_agent.constants import TICK_SECONDS, TOKEN_ENV
from mm_agent.control import ControlPlane, job_wire
from mm_agent.landing import Landed
from mm_agent.normalise import resolve
from mm_agent.types import Config, ConfigError, ControlError, Job, LandingError, Resolved
from tests.conftest import Call, StubControlPlane

NOON = 1_792_238_400_000
HOUR = 3_600_000
JOB = Job(start=NOON, end=NOON + HOUR, asset="ETH")
RESOLVED = Resolved(asset="ETH")
ORDERS = [
    ("o1", "buy", "1850.50", 0.25, "2026-10-17 12:00:00", None),
    ("o2", "sell", "1851", 1, "2026-10-17 12:30:00", "2026-10-17 12:45:00"),
]
FILLS = [
    ("o1", "2026-10-17 12:10:00", 1850.5, "0.25", "buy"),
    ("o2", "2026-10-17 14:00:00", 1851, "1", "sell"),
]
YAML = """
mm: acme
control:
  url: {url}
source:
  kind: sqlite
  dsn: sqlite:///{db}
aliases:
  assets: {{ETH: ETH}}
  venues: {{binance: BINANCE_SPOT, okx: OKX}}
  pairs: {{ETH/USDT: ETHUSDT, ETH/USDC: ETHUSDC}}
orders:
  query: select * from orders
  map: {{id: order_id, venue: venue, pair: symbol, side: side, price: price, qty: orig_qty, placed: created_at, ended: closed_at}}
fills:
  query: select * from fills
  map: {{venue: venue, pair: symbol, order: order_id, ts: exec_time, price: px, qty: qty, side: side}}
"""
FILES_YAML = """
mm: acme
control:
  url: https://control.invalid
source:
  kind: files
  dsn: {root}
aliases:
  assets: {{ETH: ETH}}
  venues: {{binance: BINANCE_SPOT, okx: OKX}}
  pairs: {{ETH/USDT: ETHUSDT, ETH/USDC: ETHUSDC}}
orders:
  query: orders.csv
  map: {{id: order_id, venue: venue, pair: symbol, side: side, price: price, qty: qty, placed: placed, ended: ended}}
fills:
  query: fills.csv
  map: {{venue: venue, pair: symbol, order: order_id, ts: ts, price: price, qty: qty, side: side}}
"""


def database(path: Path, orders: list[tuple[Any, ...]] = ORDERS) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "create table orders (order_id, side, price, orig_qty, created_at, closed_at,"
            " venue default 'BINANCE_SPOT', symbol default 'ETHUSDT')"
        )
        connection.executemany(
            "insert into orders (order_id, side, price, orig_qty, created_at, closed_at)"
            " values (?, ?, ?, ?, ?, ?)",
            orders,
        )
        connection.execute(
            "create table fills (order_id, exec_time, px, qty, side,"
            " venue default 'BINANCE_SPOT', symbol default 'ETHUSDT')"
        )
        connection.executemany(
            "insert into fills (order_id, exec_time, px, qty, side) values (?, ?, ?, ?, ?)", FILLS
        )
    return path


@pytest.fixture
def cfg(stub: StubControlPlane, tmp_path: Path) -> Config:
    return parse(YAML.format(url=stub.url, db=database(tmp_path / "oms.db")))


@pytest.fixture
def control(stub: StubControlPlane) -> ControlPlane:
    stub.respond("/jobs", 200, {"jobs": [job_wire(JOB)]})
    stub.respond("/upload_url", 200, {"url": stub.url + "/put"})
    return ControlPlane(stub.url, "tok")


def paths(stub: StubControlPlane) -> list[str]:
    return [f"{c.method} {c.path}" for c in stub.calls]


def receipts(stub: StubControlPlane) -> list[Call]:
    return [c for c in stub.calls if c.path == "/receipt"]


def test_run_happy_path(stub: StubControlPlane, cfg: Config, control: ControlPlane) -> None:
    receipt = run(cfg, control, "abc", "sha256:img")
    assert paths(stub) == [
        "POST /check_in",
        "GET /jobs",
        "POST /upload_url",
        "PUT /put",
        "POST /upload_url",
        "PUT /put",
        "POST /upload_url",
        "PUT /put",
        "POST /upload_url",
        "PUT /put",
        "POST /receipt",
        "POST /receipt",
    ]
    assert stub.calls[0].body["version"] == __version__
    assert stub.calls[0].body["config_hash"] == "abc"
    assert stub.calls[0].body["image_digest"] == "sha256:img"
    names = [c.body["name"] for c in stub.calls if c.path == "/upload_url"]
    assert names == ["orders.raw", "orders.mapped", "fills.raw", "fills.mapped"]
    job, final = (c.body for c in receipts(stub))
    assert job["kind"] == "job"
    assert job["status"] == "ok"
    assert job["target"] == {
        "asset": "ETH",
        "venue": None,
        "quote": None,
        "source_asset": "ETH",
        "source_venue": None,
        "source_pairs": None,
    }
    assert {name: f["rows"] for name, f in job["files"].items()} == {
        "orders.raw": 2,
        "orders.mapped": 2,
        "fills.raw": 2,
        "fills.mapped": 2,
    }
    assert job["error"] is None
    assert final == receipt.wire()
    assert final["jobs"] == {"attempted": 1, "succeeded": 1, "failed": 0}
    assert final["errors"] == []
    assert final["started"] <= final["ended"]


def test_put_bodies_match_receipt_hashes(stub: StubControlPlane, cfg: Config, control: ControlPlane) -> None:
    run(cfg, control, "abc", None)
    files = receipts(stub)[0].body["files"]
    puts = [c for c in stub.calls if c.method == "PUT"]
    for name, call in zip(files, puts, strict=True):
        assert isinstance(call.body, bytes)
        assert len(call.body) == files[name]["bytes"]
        assert hashlib.sha256(gzip.decompress(call.body)).hexdigest() == files[name]["sha256"]
    mapped = gzip.decompress(puts[1].body).decode().splitlines()
    assert (
        mapped[0]
        == '{"id":"o1","venue":"BINANCE_SPOT","pair":"ETHUSDT","side":"buy","price":"1850.50","qty":"0.25","placed":1792238400000,"ended":null,"filled":null,"remaining":null}'
    )


def test_run_with_no_jobs_still_checks_in_and_receipts(stub: StubControlPlane, cfg: Config) -> None:
    stub.respond("/jobs", 200, {"jobs": []})
    receipt = run(cfg, ControlPlane(stub.url, "tok"), "abc", None)
    assert paths(stub) == ["POST /check_in", "GET /jobs", "POST /receipt"]
    assert receipt.attempted == 0
    assert receipts(stub)[0].body["kind"] == "run"


def test_check_in_failure_aborts_run(stub: StubControlPlane, cfg: Config, control: ControlPlane) -> None:
    stub.respond("/check_in", 503)
    with pytest.raises(ControlError, match="POST /check_in: HTTP 503"):
        run(cfg, control, "abc", None)
    assert paths(stub) == ["POST /check_in"]


def test_missing_alias_fails_job_without_fetching(
    stub: StubControlPlane, cfg: Config, control: ControlPlane
) -> None:
    unknown = Job(start=NOON, end=NOON + HOUR, asset="ETH", venue="kraken")
    stub.respond("/jobs", 200, {"jobs": [job_wire(unknown)]})
    receipt = run(cfg, control, "abc", None)
    assert paths(stub) == ["POST /check_in", "GET /jobs", "POST /receipt", "POST /receipt"]
    job, final = (c.body for c in receipts(stub))
    assert job["status"] == "failed"
    assert job["error"] == "no alias for venue 'kraken'"
    assert job["target"]["source_asset"] is None
    assert job["files"] == {}
    assert final["jobs"] == {"attempted": 1, "succeeded": 0, "failed": 1}
    assert final["errors"] == [{"job": job_wire(unknown), "error": "no alias for venue 'kraken'"}]
    assert receipt.failed == 1


def test_normalise_error_fails_job_with_no_files(
    stub: StubControlPlane, tmp_path: Path, control: ControlPlane
) -> None:
    bad = [*ORDERS, ("o3", "hold", "1", 1, "2026-10-17 12:00:00", None)]
    cfg = parse(YAML.format(url=stub.url, db=database(tmp_path / "oms.db", bad)))
    run(cfg, control, "abc", None)
    assert "POST /upload_url" not in paths(stub)
    job = receipts(stub)[0].body
    assert job["status"] == "failed"
    assert job["error"] == "orders.side (side): unknown side 'hold'"
    assert job["files"] == {}
    assert job["target"]["source_asset"] == "ETH"


def test_driver_error_fails_job(stub: StubControlPlane, cfg: Config, control: ControlPlane) -> None:
    broken = parse(
        YAML.format(url=stub.url, db=cfg.source.dsn.removeprefix("sqlite:///")).replace("from fills", "from nope")
    )
    run(broken, control, "abc", None)
    job = receipts(stub)[0].body
    assert job["status"] == "failed"
    assert job["error"].startswith("sqlite query failed")
    assert "POST /upload_url" not in paths(stub)


def test_upload_url_failure_fails_job(stub: StubControlPlane, cfg: Config, control: ControlPlane) -> None:
    stub.respond("/upload_url", 403, raw=b"refused")
    run(cfg, control, "abc", None)
    job = receipts(stub)[0].body
    assert job["status"] == "failed"
    assert job["error"] == "POST /upload_url: HTTP 403: refused"
    assert job["files"] == {}


def test_put_failure_keeps_files_already_landed(cfg: Config) -> None:
    landed: list[str] = []

    def deliver(file: Landed) -> None:
        if len(landed) == 2:
            raise LandingError(f"PUT {file.name}: HTTP 403: expired")
        landed.append(file.name)

    receipt = land(cfg, JOB, deliver)
    assert receipt.error == "PUT fills.raw: HTTP 403: expired"
    assert [f.name for f in receipt.files] == ["orders.raw", "orders.mapped"]
    assert receipt.query_ms >= 0


def test_unexpected_error_fails_the_job_with_a_receipt(
    stub: StubControlPlane, cfg: Config, control: ControlPlane, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: object) -> tuple[Landed, ...]:
        raise RuntimeError("boom")

    monkeypatch.setattr("mm_agent.__main__.fetch", explode)
    assert process(cfg, control, JOB) == "RuntimeError: boom"
    assert paths(stub) == ["POST /receipt"]
    assert stub.calls[0].body["status"] == "failed"
    assert stub.calls[0].body["error"] == "RuntimeError: boom"
    assert stub.calls[0].body["target"]["source_asset"] == "ETH"
    assert stub.calls[0].body["target"]["source_venue"] is None


def test_unexpected_error_during_put_keeps_files_already_landed(
    cfg: Config, caplog: pytest.LogCaptureFixture
) -> None:
    landed: list[str] = []

    def deliver(file: Landed) -> None:
        if len(landed) == 2:
            raise RuntimeError("boom")
        landed.append(file.name)

    receipt = land(cfg, JOB, deliver)
    assert receipt.error == "RuntimeError: boom"
    assert receipt.resolved is not None
    assert [f.name for f in receipt.files] == ["orders.raw", "orders.mapped"]
    assert "crashed" in caplog.text
    assert "Traceback" in caplog.text


def test_row_count_logs_mapped_rows_only(
    stub: StubControlPlane, cfg: Config, control: ControlPlane, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    assert process(cfg, control, JOB) is None
    assert "4 rows in" in caplog.text


def test_undeliverable_job_receipt_counts_as_error(
    stub: StubControlPlane, cfg: Config, control: ControlPlane
) -> None:
    stub.respond("/receipt", 500, raw=b"down")
    assert process(cfg, control, JOB) == "POST /receipt: HTTP 500: down"


def test_undeliverable_receipt_keeps_job_error(stub: StubControlPlane, cfg: Config, control: ControlPlane) -> None:
    stub.respond("/receipt", 500, raw=b"down")
    unknown = Job(start=NOON, end=NOON + HOUR, asset="BTC")
    assert process(cfg, control, unknown) == "no alias for asset 'BTC'; POST /receipt: HTTP 500: down"


def test_fetch_from_files_applies_window_filter(tmp_path: Path) -> None:
    (tmp_path / "orders.csv").write_text(
        "order_id,venue,symbol,side,price,qty,placed,ended\n"
        "o1,BINANCE_SPOT,ETHUSDT,buy,1,1,2026-10-17T12:00:00Z,\n"
        "o2,BINANCE_SPOT,ETHUSDT,sell,1,1,2026-10-17T13:00:00Z,\n"
    )
    (tmp_path / "fills.csv").write_text(
        "order_id,venue,symbol,ts,price,qty,side\n"
        "o1,BINANCE_SPOT,ETHUSDT,2026-10-17T12:59:59Z,1,1,buy\n"
        "o2,BINANCE_SPOT,ETHUSDT,2026-10-17T13:00:00Z,1,1,sell\n"
    )
    cfg = parse(FILES_YAML.format(root=tmp_path))
    spooled = fetch(cfg, JOB, RESOLVED, tmp_path)
    assert {f.name: f.rows for f in spooled} == {
        "orders.raw": 1,
        "orders.mapped": 1,
        "fills.raw": 1,
        "fills.mapped": 1,
    }


def test_fetch_from_sql_keeps_every_cursor_row(cfg: Config, tmp_path: Path) -> None:
    spooled = fetch(cfg, JOB, RESOLVED, tmp_path)
    assert [f.rows for f in spooled] == [2, 2, 2, 2]
    assert [f.name for f in spooled] == ["orders.raw", "orders.mapped", "fills.raw", "fills.mapped"]


MARKETS = [
    ("o1", "BINANCE_SPOT", "ETHUSDT"),
    ("o2", "BINANCE_SPOT", "ETHUSDC"),
    ("o3", "OKX", "ETHUSDT"),
    ("o4", "OKX", "ETHUSDC"),
]
NARROWING = [
    (None, None, ["o1", "o2", "o3", "o4"]),
    ("binance", None, ["o1", "o2"]),
    (None, "USDT", ["o1", "o3"]),
    ("okx", "USDC", ["o4"]),
    ("binance", "USDT", ["o1"]),
]


def multi_market_database(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "create table orders (order_id, venue, symbol, side, price, orig_qty, created_at, closed_at)"
        )
        connection.executemany(
            "insert into orders values (?, ?, ?, 'buy', '1', '1', '2026-10-17 12:00:00', null)", MARKETS
        )
        connection.execute("create table fills (order_id, venue, symbol, exec_time, px, qty, side)")
        connection.executemany(
            "insert into fills values (?, ?, ?, '2026-10-17 12:10:00', '1', '1', 'buy')", MARKETS
        )
    return path


def mapped_ids(spooled: tuple[Landed, ...], name: str, column: str) -> list[str]:
    file = next(f for f in spooled if f.name == name)
    with gzip.open(file.path, "rt", encoding="utf-8") as handle:
        return [json.loads(line)[column] for line in handle]


@pytest.mark.parametrize(("venue", "quote", "expected"), NARROWING)
def test_sql_job_lands_only_its_venue_and_quote(
    stub: StubControlPlane, tmp_path: Path, venue: str | None, quote: str | None, expected: list[str]
) -> None:
    cfg = parse(YAML.format(url=stub.url, db=multi_market_database(tmp_path / "oms.db")))
    job = Job(NOON, NOON + HOUR, "ETH", venue, quote)
    spooled = fetch(cfg, job, resolve(cfg.aliases, job), tmp_path)
    assert mapped_ids(spooled, "orders.mapped", "id") == expected
    assert mapped_ids(spooled, "orders.raw", "order_id") == expected
    assert mapped_ids(spooled, "fills.mapped", "order") == expected
    assert mapped_ids(spooled, "fills.raw", "order_id") == expected


@pytest.mark.parametrize(("venue", "quote", "expected"), NARROWING)
def test_files_job_lands_only_its_venue_and_quote(
    tmp_path: Path, venue: str | None, quote: str | None, expected: list[str]
) -> None:
    (tmp_path / "orders.csv").write_text(
        "order_id,venue,symbol,side,price,qty,placed,ended\n"
        + "".join(f"{o},{v},{s},buy,1,1,2026-10-17T12:00:00Z,\n" for o, v, s in MARKETS)
    )
    (tmp_path / "fills.csv").write_text(
        "order_id,venue,symbol,ts,price,qty,side\n"
        + "".join(f"{o},{v},{s},2026-10-17T12:10:00Z,1,1,buy\n" for o, v, s in MARKETS)
    )
    cfg = parse(FILES_YAML.format(root=tmp_path))
    job = Job(NOON, NOON + HOUR, "ETH", venue, quote)
    spooled = fetch(cfg, job, resolve(cfg.aliases, job), tmp_path)
    assert mapped_ids(spooled, "orders.mapped", "id") == expected
    assert mapped_ids(spooled, "orders.raw", "order_id") == expected
    assert mapped_ids(spooled, "fills.mapped", "order") == expected
    assert mapped_ids(spooled, "fills.raw", "order_id") == expected


def test_load_hashes_file_bytes(tmp_path: Path, valid_yaml: str) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(valid_yaml)
    cfg, digest = load(path)
    assert cfg.mm == "acme"
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


def test_load_missing_file_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load(tmp_path / "missing.yaml")


def test_main_requires_token(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    assert main([]) == 2
    assert f"{TOKEN_ENV} is not set" in caplog.text


def test_main_rejects_bad_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(TOKEN_ENV, "tok")
    path = tmp_path / "agent.yaml"
    path.write_text("mm: 1\n")
    assert main([str(path)]) == 2
    assert "mm: expected a non-empty string" in caplog.text


class Stop(Exception):
    pass


def test_main_loops_on_tick(
    stub: StubControlPlane, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(YAML.format(url=stub.url, db=database(tmp_path / "oms.db")))
    stub.respond("/jobs", 200, {"jobs": []})
    monkeypatch.setenv(TOKEN_ENV, "tok")
    monkeypatch.setenv("FORGD_IMAGE_DIGEST", "sha256:img")
    caplog.set_level(logging.INFO)
    naps: list[float] = []

    def sleep(seconds: float) -> None:
        naps.append(seconds)
        if len(naps) == 2:
            raise Stop

    monkeypatch.setattr("mm_agent.__main__.time.sleep", sleep)
    with pytest.raises(Stop):
        main([str(path)])
    assert naps == [TICK_SECONDS, TICK_SECONDS]
    assert paths(stub) == ["POST /check_in", "GET /jobs", "POST /receipt"] * 2
    assert stub.calls[0].body["image_digest"] == "sha256:img"
    assert stub.calls[0].body["config_hash"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "0 job(s) outstanding" in caplog.text


def test_main_logs_control_failure_and_keeps_looping(
    stub: StubControlPlane, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(YAML.format(url=stub.url, db=database(tmp_path / "oms.db")))
    stub.respond("/check_in", 503)
    monkeypatch.setenv(TOKEN_ENV, "tok")
    monkeypatch.setattr("mm_agent.__main__.time.sleep", lambda seconds: (_ for _ in ()).throw(Stop()))
    with pytest.raises(Stop):
        main([str(path)])
    assert "run failed: POST /check_in: HTTP 503" in caplog.text


def test_main_logs_unexpected_crash_and_keeps_looping(
    stub: StubControlPlane, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(YAML.format(url=stub.url, db=database(tmp_path / "oms.db")))
    monkeypatch.setenv(TOKEN_ENV, "tok")
    monkeypatch.setattr("mm_agent.__main__.run", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("mm_agent.__main__.time.sleep", lambda seconds: (_ for _ in ()).throw(Stop()))
    with pytest.raises(Stop):
        main([str(path)])
    assert "run crashed" in caplog.text
    assert "RuntimeError: boom" in caplog.text
