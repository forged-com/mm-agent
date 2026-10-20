from pathlib import Path

from mm_agent.landing import Landed
from mm_agent.receipts import JobReceipt, RunError, RunReceipt
from mm_agent.types import Asset, Job

JOB = Job(start=1_760_702_400_000, end=1_760_706_000_000, pair="ETH/USDT", venue="binance")
ASSET = Asset(pair="ETHUSDT", venue="BINANCE_SPOT")
ORDERS_RAW = Landed("orders.raw", Path("/tmp/x/orders.raw.jsonl.gz"), 12, 345, "a" * 64)
ORDERS_MAPPED = Landed("orders.mapped", Path("/tmp/x/orders.mapped.jsonl.gz"), 12, 300, "b" * 64)


def test_job_receipt_ok() -> None:
    receipt = JobReceipt(JOB, ASSET, (ORDERS_RAW, ORDERS_MAPPED), query_ms=42, warnings=("slow",))
    assert receipt.wire() == {
        "kind": "job",
        "status": "ok",
        "asset": {
            "pair": "ETH/USDT",
            "venue": "binance",
            "source_pair": "ETHUSDT",
            "source_venue": "BINANCE_SPOT",
        },
        "window": {"from": JOB.start, "to": JOB.end},
        "files": {
            "orders.raw": {"rows": 12, "bytes": 345, "sha256": "a" * 64},
            "orders.mapped": {"rows": 12, "bytes": 300, "sha256": "b" * 64},
        },
        "query_ms": 42,
        "warnings": ["slow"],
        "error": None,
    }


def test_job_receipt_failed_without_alias() -> None:
    receipt = JobReceipt(JOB, None, (), query_ms=0, error="no alias for ETH/USDT on binance")
    wire = receipt.wire()
    assert wire["status"] == "failed"
    assert wire["error"] == "no alias for ETH/USDT on binance"
    assert wire["asset"]["source_pair"] is None
    assert wire["asset"]["source_venue"] is None
    assert wire["files"] == {}
    assert wire["warnings"] == []


def test_job_receipt_failed_keeps_landed_files() -> None:
    receipt = JobReceipt(JOB, ASSET, (ORDERS_RAW,), query_ms=7, error="cursor died")
    wire = receipt.wire()
    assert wire["status"] == "failed"
    assert list(wire["files"]) == ["orders.raw"]


def test_job_receipt_wire_is_json_plain() -> None:
    wire = JobReceipt(JOB, ASSET, (ORDERS_RAW,), query_ms=1).wire()
    assert all(isinstance(k, str) for k in wire)
    assert not any(isinstance(v, Path) for f in wire["files"].values() for v in f.values())


def test_run_receipt() -> None:
    receipt = RunReceipt(
        version="0.1.0",
        config_hash="cfg",
        image_digest="sha256:abc",
        started=1_760_702_400_000,
        ended=1_760_702_460_000,
        attempted=3,
        succeeded=1,
        errors=(RunError(JOB, "cursor died"), RunError(None, "GET /jobs: HTTP 503")),
    )
    assert receipt.failed == 2
    assert receipt.wire() == {
        "kind": "run",
        "version": "0.1.0",
        "config_hash": "cfg",
        "image_digest": "sha256:abc",
        "started": 1_760_702_400_000,
        "ended": 1_760_702_460_000,
        "jobs": {"attempted": 3, "succeeded": 1, "failed": 2},
        "errors": [
            {
                "job": {"from": JOB.start, "to": JOB.end, "pair": "ETH/USDT", "venue": "binance"},
                "error": "cursor died",
            },
            {"job": None, "error": "GET /jobs: HTTP 503"},
        ],
    }


def test_run_receipt_clean_run() -> None:
    wire = RunReceipt("0.1.0", "cfg", None, 1, 2, attempted=0, succeeded=0).wire()
    assert wire["jobs"] == {"attempted": 0, "succeeded": 0, "failed": 0}
    assert wire["errors"] == []
    assert wire["image_digest"] is None
