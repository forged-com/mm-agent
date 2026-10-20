import gzip
import hashlib
import json
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from mm_agent.landing import Landed, Writer, put
from mm_agent.types import Fill, LandingError, Order
from tests.conftest import StubControlPlane

ORDER = Order(id="o1", side="buy", price="1.50", qty="2", placed=1_760_702_400_000, ended=None)
FILL = Fill(order="o1", ts=1_760_702_400_500, price="1.50", qty="1", side="buy")


def lines(path: Path) -> list[str]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return handle.read().splitlines()


def raw_bytes(path: Path) -> bytes:
    with gzip.open(path, "rb") as handle:
        return handle.read()


def land(directory: Path, name: str, rows: list[object]) -> Landed:
    with Writer(directory, name) as writer:
        for row in rows:
            writer.write(row)
    return writer.close()


def test_writes_gzipped_jsonl_named_after_landing_name(tmp_path: Path) -> None:
    landed = land(tmp_path, "orders.raw", [{"a": 1}, {"b": "x"}])
    assert landed.path == tmp_path / "orders.raw.jsonl.gz"
    assert lines(landed.path) == ['{"a":1}', '{"b":"x"}']


def test_counts_rows_bytes_and_hashes_uncompressed_content(tmp_path: Path) -> None:
    landed = land(tmp_path, "fills.mapped", [{"a": 1}, {"b": 2}, {"c": 3}])
    content = raw_bytes(landed.path)
    assert landed.name == "fills.mapped"
    assert landed.rows == 3
    assert landed.bytes == landed.path.stat().st_size
    assert landed.sha256 == hashlib.sha256(content).hexdigest()
    assert content == b'{"a":1}\n{"b":2}\n{"c":3}\n'


def test_empty_file_is_valid_gzip_with_empty_hash(tmp_path: Path) -> None:
    landed = land(tmp_path, "orders.raw", [])
    assert landed.rows == 0
    assert raw_bytes(landed.path) == b""
    assert landed.sha256 == hashlib.sha256(b"").hexdigest()


def test_gzip_bytes_reproducible_across_runs(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = land(tmp_path / "a", "orders.raw", [{"a": 1}])
    second = land(tmp_path / "b", "orders.raw", [{"a": 1}])
    assert first.path.read_bytes() == second.path.read_bytes()
    assert first.sha256 == second.sha256


def test_mapped_dataclasses_serialise_in_schema_order(tmp_path: Path) -> None:
    landed = land(tmp_path, "orders.mapped", [ORDER])
    assert lines(landed.path) == [
        '{"id":"o1","side":"buy","price":"1.50","qty":"2","placed":1760702400000,"ended":null,"filled":null,"remaining":null}'
    ]
    landed = land(tmp_path, "fills.mapped", [FILL])
    assert lines(landed.path) == [
        f'{{"id":"{FILL.id}","order":"o1","ts":1760702400500,"price":"1.50","qty":"1","side":"buy"}}'
    ]


def test_raw_cursor_types_serialise(tmp_path: Path) -> None:
    row = {
        "aware": datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
        "naive": datetime(2026, 10, 17, 12, 0, tzinfo=UTC).replace(tzinfo=None),
        "day": date(2026, 10, 17),
        "clock": time(12, 30),
        "price": Decimal("1.50"),
        "uid": UUID("12345678-1234-5678-1234-567812345678"),
        "blob": b"\x00\x01",
        "none": None,
        "text": "ünïcode",
        "number": 1.5,
    }
    landed = land(tmp_path, "orders.raw", [row])
    assert json.loads(lines(landed.path)[0]) == {
        "aware": "2026-10-17T12:00:00+00:00",
        "naive": "2026-10-17T12:00:00",
        "day": "2026-10-17",
        "clock": "12:30:00",
        "price": "1.50",
        "uid": "12345678-1234-5678-1234-567812345678",
        "blob": "0001",
        "none": None,
        "text": "ünïcode",
        "number": 1.5,
    }
    assert "ünïcode" in lines(landed.path)[0]


def test_raw_key_order_preserved(tmp_path: Path) -> None:
    landed = land(tmp_path, "orders.raw", [{"z": 1, "a": 2}])
    assert lines(landed.path) == ['{"z":1,"a":2}']


def test_close_is_idempotent(tmp_path: Path) -> None:
    writer = Writer(tmp_path, "orders.raw")
    writer.write({"a": 1})
    first = writer.close()
    assert writer.close() is first


def test_unknown_name_rejected(tmp_path: Path) -> None:
    with pytest.raises(LandingError, match=r"unknown landing name 'trades.raw'; known are \['fills.mapped'"):
        Writer(tmp_path, "trades.raw")


def test_unwritable_directory_rejected(tmp_path: Path) -> None:
    with pytest.raises(LandingError, match=r"orders\.raw\.jsonl\.gz"):
        Writer(tmp_path / "missing", "orders.raw")


def test_put_streams_file_with_content_type_and_length(stub: StubControlPlane, tmp_path: Path) -> None:
    landed = land(tmp_path, "orders.raw", [{"a": 1}, {"b": 2}])
    put(stub.url + "/signed?x=1", landed)
    call = stub.calls[0]
    assert call.method == "PUT"
    assert call.path == "/signed?x=1"
    assert call.headers["Content-Type"] == "application/gzip"
    assert call.headers["Content-Length"] == str(landed.bytes)
    assert "Authorization" not in call.headers
    assert call.body == landed.path.read_bytes()


def test_put_sends_empty_file(stub: StubControlPlane, tmp_path: Path) -> None:
    landed = land(tmp_path, "fills.raw", [])
    put(stub.url + "/signed", landed)
    assert stub.calls[0].headers["Content-Length"] == str(landed.bytes)
    assert stub.calls[0].body == landed.path.read_bytes()


@pytest.mark.parametrize("status", [400, 403, 404, 500])
def test_put_http_error_names_file_not_url(stub: StubControlPlane, tmp_path: Path, status: int) -> None:
    landed = land(tmp_path, "orders.mapped", [])
    stub.respond("/signed", status, raw=b"ExpiredToken")
    with pytest.raises(LandingError, match=f"^PUT orders.mapped: HTTP {status}: ExpiredToken$"):
        put(stub.url + "/signed", landed)


@pytest.mark.parametrize("value", [b"\x00\xff", bytearray(b"\x00\xff"), memoryview(b"\x00\xff")])
def test_bytes_like_values_land_as_hex(tmp_path: Path, value: object) -> None:
    landed = land(tmp_path, "orders.raw", [{"blob": value}])
    assert lines(landed.path) == ['{"blob":"00ff"}']


def test_exit_keeps_the_original_error_when_close_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="original"), Writer(tmp_path, "orders.raw") as writer:
        monkeypatch.setattr(writer.handle, "close", lambda: (_ for _ in ()).throw(OSError("disk")))
        raise ValueError("original")


def test_put_unreachable_raises(tmp_path: Path) -> None:
    landed = land(tmp_path, "orders.raw", [])
    with pytest.raises(LandingError, match=r"^PUT orders\.raw: "):
        put("http://127.0.0.1:9/signed", landed)


def test_put_missing_file_raises(tmp_path: Path) -> None:
    landed = land(tmp_path, "orders.raw", [])
    landed.path.unlink()
    with pytest.raises(LandingError, match=r"^PUT orders\.raw: "):
        put("http://127.0.0.1:9/signed", landed)


def _boom(*args: object) -> None:
    raise OSError("disk full")


def test_write_io_error_is_landing_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writer = Writer(tmp_path, "orders.raw")
    monkeypatch.setattr(writer.handle, "write", _boom)
    with pytest.raises(LandingError, match=r"orders\.raw\.jsonl\.gz: disk full"):
        writer.write({"a": 1})


def test_close_io_error_is_landing_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writer = Writer(tmp_path, "orders.raw")
    writer.write({"a": 1})
    monkeypatch.setattr(writer.handle, "close", _boom)
    with pytest.raises(LandingError, match=r"orders\.raw\.jsonl\.gz: disk full"):
        writer.close()
