import gzip
import hashlib
from pathlib import Path
from typing import Any

import pytest

from mm_agent.__main__ import run
from mm_agent.control import ControlPlane, job_wire
from mm_agent.types import Config, ControlError, Job, SourceKind
from tests.e2e.conftest import TOKEN
from tests.e2e.fake_control import FakeControlPlane
from tests.e2e.fake_landing import FakeLanding
from tests.e2e.test_happy_path import IMAGE_DIGEST, NAMES, configured, object_key
from tests.fixtures import golden

FILLS_SELECT = "select order_id, venue, symbol, exec_time, px, qty, side"
POISONED_SELECT = (
    f"{FILLS_SELECT}, abs(case when row_number() over (order by exec_time, order_id) = 4"
    " then -9223372036854775808 else 0 end) as poison"
)
BTC, ETH = golden.jobs()
STRANGER = Job(golden.START, golden.END, "ETH", "okx")
ORDERS = ("orders.raw", "orders.mapped")


def tick(cfg: Config, config_hash: str, control: FakeControlPlane) -> dict[str, Any]:
    receipt = run(cfg, ControlPlane(cfg.control.url, TOKEN), config_hash, IMAGE_DIGEST)
    assert control.receipts[-1] == receipt.wire()
    assert [r["kind"] for r in control.receipts] == ["job"] * receipt.attempted + ["run"]
    return control.receipts[-1]


def job_receipt(control: FakeControlPlane, job: Job) -> dict[str, Any]:
    return next(
        r
        for r in control.receipts
        if r["kind"] == "job" and (r["target"]["asset"], r["target"]["venue"]) == (job.asset, job.venue)
    )


def landed(landing: FakeLanding, job: Job) -> list[str]:
    return sorted(name for name in NAMES if object_key(job, name) in landing.objects)


def test_control_plane_down_lands_nothing_and_records_nothing(
    control: FakeControlPlane, landing: FakeLanding, tmp_path: Path
) -> None:
    cfg, config_hash = configured(SourceKind.SQLITE, control, tmp_path)
    control.seed(BTC, ETH)
    control.stop()
    with pytest.raises(ControlError, match="POST /check_in"):
        run(cfg, ControlPlane(cfg.control.url, TOKEN), config_hash, IMAGE_DIGEST)
    assert control.check_ins == [] and control.uploads == [] and control.receipts == []
    assert landing.objects == {}
    assert control.outstanding == [job_wire(BTC), job_wire(ETH)]


def test_db_dying_mid_cursor_fails_the_job_with_no_files(
    control: FakeControlPlane, landing: FakeLanding, tmp_path: Path
) -> None:
    cfg, config_hash = configured(SourceKind.SQLITE, control, tmp_path, (FILLS_SELECT, POISONED_SELECT))
    control.seed(BTC, ETH)
    run_receipt = tick(cfg, config_hash, control)
    for job in (BTC, ETH):
        receipt = job_receipt(control, job)
        assert receipt["status"] == "failed"
        assert receipt["error"].startswith("sqlite query failed: ") and "integer overflow" in receipt["error"]
        assert receipt["files"] == {}
        assert receipt["target"]["source_asset"] == golden.ASSETS[job.asset]
    assert control.uploads == [] and landing.objects == {}
    assert run_receipt["jobs"] == {"attempted": 2, "succeeded": 0, "failed": 2}
    assert [e["job"] for e in run_receipt["errors"]] == [job_wire(BTC), job_wire(ETH)]
    assert control.outstanding == [job_wire(BTC), job_wire(ETH)] and control.completed == []


def test_missing_alias_fails_only_that_job(
    control: FakeControlPlane, landing: FakeLanding, tmp_path: Path
) -> None:
    cfg, config_hash = configured(SourceKind.SQLITE, control, tmp_path)
    control.seed(BTC, ETH, STRANGER)
    run_receipt = tick(cfg, config_hash, control)
    receipt = job_receipt(control, STRANGER)
    assert receipt["status"] == "failed" and receipt["error"] == "no alias for venue 'okx'"
    assert receipt["target"] == {
        "asset": "ETH",
        "venue": "okx",
        "quote": None,
        "source_asset": None,
        "source_venue": None,
        "source_pairs": None,
    }
    assert receipt["files"] == {} and receipt["query_ms"] == 0
    for job in (BTC, ETH):
        assert job_receipt(control, job)["status"] == "ok"
        assert landed(landing, job) == sorted(NAMES)
    assert landed(landing, STRANGER) == []
    assert run_receipt["jobs"] == {"attempted": 3, "succeeded": 2, "failed": 1}
    assert run_receipt["errors"] == [{"job": job_wire(STRANGER), "error": "no alias for venue 'okx'"}]
    assert control.outstanding == [job_wire(STRANGER)]
    assert control.completed == [job_wire(BTC), job_wire(ETH)]


def test_already_done_job_is_refused_before_any_put(
    control: FakeControlPlane, landing: FakeLanding, tmp_path: Path
) -> None:
    cfg, config_hash = configured(SourceKind.SQLITE, control, tmp_path)
    control.seed(BTC, ETH)
    control.done(ETH)
    run_receipt = tick(cfg, config_hash, control)
    receipt = job_receipt(control, ETH)
    assert receipt["status"] == "failed"
    assert receipt["error"].startswith("POST /upload_urls: HTTP 409") and "job already done" in receipt["error"]
    assert receipt["files"] == {}
    assert control.uploads == [job_wire(BTC)]
    assert landed(landing, BTC) == sorted(NAMES) and landed(landing, ETH) == []
    assert run_receipt["jobs"] == {"attempted": 2, "succeeded": 1, "failed": 1}
    assert run_receipt["errors"] == [{"job": job_wire(ETH), "error": receipt["error"]}]
    assert control.outstanding == [job_wire(ETH)]
    assert control.completed == [job_wire(ETH), job_wire(BTC)]


def test_expired_url_keeps_only_files_already_put(
    control: FakeControlPlane, landing: FakeLanding, tmp_path: Path
) -> None:
    cfg, config_hash = configured(SourceKind.SQLITE, control, tmp_path)
    control.seed(BTC, ETH)
    landing.expire(object_key(ETH, "fills.raw"))
    run_receipt = tick(cfg, config_hash, control)
    receipt = job_receipt(control, ETH)
    assert receipt["status"] == "failed"
    assert receipt["error"].startswith("PUT fills.raw: HTTP 403") and "signature expired" in receipt["error"]
    assert sorted(receipt["files"]) == sorted(ORDERS)
    for name, file in receipt["files"].items():
        stored = landing.objects[object_key(ETH, name)]
        assert file["bytes"] == len(stored.body)
        assert file["sha256"] == hashlib.sha256(gzip.decompress(stored.body)).hexdigest()
    assert landed(landing, ETH) == sorted(ORDERS) and landed(landing, BTC) == sorted(NAMES)
    assert [u for u in control.uploads if u["asset"] == ETH.asset] == [job_wire(ETH)]
    assert run_receipt["jobs"] == {"attempted": 2, "succeeded": 1, "failed": 1}
    assert run_receipt["errors"] == [{"job": job_wire(ETH), "error": receipt["error"]}]
    assert control.outstanding == [job_wire(ETH)] and control.completed == [job_wire(BTC)]
