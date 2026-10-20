import json
from io import StringIO
from pathlib import Path

import pytest

from mm_agent.__main__ import main
from mm_agent.config import parse
from mm_agent.constants import TOKEN_ENV
from mm_agent.dryrun import Report, jobs, moment, utc_day, window
from mm_agent.landing import Writer
from mm_agent.types import Job
from tests.conftest import StubControlPlane
from tests.unit.test_main import FILES_YAML, HOUR, NOON, YAML, database

DAY = 24 * HOUR
MIDNIGHT = NOON - 12 * HOUR
BERLIN_YAML = YAML.replace("  dsn: sqlite:///{db}\n", "  dsn: sqlite:///{db}\n  timezone: Europe/Berlin\n")


def dry_run(
    tmp_path: Path, yaml: str, *flags: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    path = tmp_path / "agent.yaml"
    path.write_text(yaml)
    code = main([str(path), "--dry-run", *flags])
    return code, capsys.readouterr().out


def test_moment_reads_offsets_and_naive_as_utc() -> None:
    assert moment("2026-10-17T12:00Z") == NOON
    assert moment("2026-10-17T14:00:00+02:00") == NOON
    assert moment("2026-10-17T12:00:00") == NOON
    assert moment("2026-10-17T12:00:00.250Z") == NOON + 250


def test_moment_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        moment("yesterday")


def test_utc_day_spans_the_day() -> None:
    assert utc_day("2026-10-17") == (MIDNIGHT, MIDNIGHT + DAY)
    with pytest.raises(ValueError):
        utc_day("2026-10-17T12:00")


def test_window_combinations() -> None:
    assert window(None, None, None, None) is None
    assert window("--dry-run", NOON, NOON + HOUR, None) == (NOON, NOON + HOUR)
    assert window("--dry-run", None, None, (MIDNIGHT, MIDNIGHT + DAY)) == (MIDNIGHT, MIDNIGHT + DAY)
    with pytest.raises(ValueError, match="need --dry-run or --conform"):
        window(None, NOON, None, None)
    with pytest.raises(ValueError, match="--conform needs --from and --to"):
        window("--conform", NOON, None, None)
    with pytest.raises(ValueError, match="cannot be combined"):
        window("--dry-run", NOON, NOON + HOUR, (MIDNIGHT, MIDNIGHT + DAY))
    with pytest.raises(ValueError, match="before --to"):
        window("--dry-run", NOON, NOON, None)


def test_jobs_cover_every_pair_on_every_venue(valid_yaml: str) -> None:
    yaml = valid_yaml.replace("venues: {binance: BINANCE_SPOT}", "venues: {okx: OKX, binance: BINANCE_SPOT}")
    yaml = yaml.replace("pairs: {ETH/USDT: ETHUSDT}", "pairs: {ETH/USDT: ETHUSDT, BTC/USDT: BTCUSDT}")
    assert jobs(parse(yaml), NOON, NOON + HOUR) == [
        Job(NOON, NOON + HOUR, "BTC/USDT", "binance"),
        Job(NOON, NOON + HOUR, "BTC/USDT", "okx"),
        Job(NOON, NOON + HOUR, "ETH/USDT", "binance"),
        Job(NOON, NOON + HOUR, "ETH/USDT", "okx"),
    ]


def test_dry_run_prints_the_report_without_touching_the_network(
    stub: StubControlPlane, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    yaml = YAML.format(url=stub.url, db=database(tmp_path / "oms.db"))
    code, out = dry_run(tmp_path, yaml, "--day", "2026-10-17", monkeypatch=monkeypatch, capsys=capsys)
    assert code == 0
    assert stub.calls == []
    lines = out.splitlines()
    assert lines[0].startswith("forgd-mm-agent ") and lines[0].endswith(" dry run for acme")
    assert lines[2] == "source sqlite, timezone UTC"
    assert lines[3] == f"window [2026-10-17T00:00:00Z, 2026-10-18T00:00:00Z) = [{MIDNIGHT}, {MIDNIGHT + DAY})"
    assert lines[4] == "1 job(s): every configured pair on every configured venue"
    assert "job ETH/USDT on binance" in lines
    assert "  pair ETH/USDT -> ETHUSDT" in lines
    assert "  venue binance -> BINANCE_SPOT" in lines
    assert "    select * from orders" in lines
    assert "    :from = 2026-10-17T00:00:00 (UTC)" in lines
    assert "    :to_utc = 2026-10-18T00:00:00+00:00" in lines
    assert "    :pair = 'ETHUSDT'" in lines
    assert "    :venue = 'BINANCE_SPOT'" in lines
    assert any(line.startswith("  orders.raw: 2 row(s), ") and "sha256" in line for line in lines)
    assert any(line.startswith("  fills.mapped: 2 row(s), ") for line in lines)
    assert '    {"order_id":"o1","side":"buy","price":"1850.50","orig_qty":0.25,' in out
    assert (
        '    {"id":"o1","side":"buy","price":"1850.50","qty":"0.25","placed":1792238400000,"ended":null,"filled":null,"remaining":null}'
        in lines
    )
    assert "  job receipt (not sent):" in lines
    assert "run receipt (not sent):" in lines
    assert '      "status": "ok",' in lines
    assert '    "jobs": {' in lines
    assert out.count('"kind": "job"') == 1 and out.count('"kind": "run"') == 1


def test_dry_run_shows_bound_window_in_source_timezone(
    stub: StubControlPlane, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    yaml = BERLIN_YAML.format(url=stub.url, db=database(tmp_path / "oms.db"))
    flags = ("--from", "2026-10-17T12:00Z", "--to", "2026-10-17T13:00Z")
    code, out = dry_run(tmp_path, yaml, *flags, monkeypatch=monkeypatch, capsys=capsys)
    assert code == 0
    assert "    :from = 2026-10-17T14:00:00 (Europe/Berlin)" in out
    assert "    :from_utc = 2026-10-17T12:00:00+00:00" in out
    assert "    :to = 2026-10-17T15:00:00 (Europe/Berlin)" in out


def test_dry_run_lists_matched_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "exports"
    root.mkdir()
    (root / "orders.csv").write_text("order_id,side,price,qty,placed,ended\no1,buy,1,1,2026-10-17T12:00:00Z,\n")
    (root / "fills.csv").write_text("order_id,ts,price,qty,side\n")
    yaml = FILES_YAML.format(root=root).replace("query: fills.csv", "query: fills/{from}/*.csv")
    flags = ("--from", "2026-10-17T12:00Z", "--to", "2026-10-17T13:00Z")
    code, out = dry_run(tmp_path, yaml, *flags, monkeypatch=monkeypatch, capsys=capsys)
    assert code == 0
    lines = out.splitlines()
    assert "  orders glob 'orders.csv' matches 1 file(s)" in lines
    assert f"    {root / 'orders.csv'}" in lines
    assert "  fills glob 'fills/2026-10-17/*.csv' matches 0 file(s)" in lines
    assert any(line.startswith("  orders.mapped: 1 row(s), ") for line in lines)
    assert any(line.startswith("  fills.raw: 0 row(s), ") for line in lines)


def test_dry_run_failed_job_exits_1(
    stub: StubControlPlane, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    orders = [("o1", "hold", "1", 1, "2026-10-17 12:00:00", None)]
    yaml = YAML.format(url=stub.url, db=database(tmp_path / "oms.db", orders))
    code, out = dry_run(tmp_path, yaml, "--day", "2026-10-17", monkeypatch=monkeypatch, capsys=capsys)
    assert code == 1
    assert stub.calls == []
    assert '      "status": "failed",' in out
    assert "orders.side (side): unknown side 'hold'" in out
    assert '"attempted": 1' in out and '"failed": 1' in out


def test_dry_run_needs_a_window(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([str(tmp_path / "agent.yaml"), "--dry-run"])
    assert exit_info.value.code == 2
    assert "--dry-run needs --from and --to, or --day" in capsys.readouterr().err


def test_window_flags_need_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([str(tmp_path / "agent.yaml"), "--day", "2026-10-17"])
    assert exit_info.value.code == 2
    assert "--from, --to and --day need --dry-run or --conform" in capsys.readouterr().err


def test_bad_instant_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([str(tmp_path / "agent.yaml"), "--dry-run", "--from", "noon", "--to", "2026-10-17T13:00Z"])
    assert exit_info.value.code == 2
    assert "argument --from: invalid moment value: 'noon'" in capsys.readouterr().err


def test_report_samples_the_first_rows(tmp_path: Path) -> None:
    with Writer(tmp_path, "orders.raw") as writer:
        for n in range(7):
            writer.write({"n": n})
    out = StringIO()
    Report(out).file(writer.close())
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("  orders.raw: 7 row(s), ")
    assert [json.loads(line) for line in lines[1:6]] == [{"n": n} for n in range(5)]
    assert lines[6] == "    ... 2 more"
    assert len(lines) == 7


def test_dry_run_reports_a_bad_glob_and_fails_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "exports"
    root.mkdir()
    yaml = FILES_YAML.format(root=root).replace("query: orders.csv", "query: orders/{date}.csv")
    code, out = dry_run(tmp_path, yaml, "--day", "2026-10-17", monkeypatch=monkeypatch, capsys=capsys)
    assert code == 1
    assert "  orders files: glob 'orders/{date}.csv': unknown placeholder {date}; known are" in out
    assert "unknown placeholder {date}" in out.split("job receipt (not sent):")[1]
