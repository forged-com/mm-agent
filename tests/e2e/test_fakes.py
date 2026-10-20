import gzip
import hashlib
from pathlib import Path

import pytest
import requests

from mm_agent.control import ControlPlane
from mm_agent.landing import Writer, put
from mm_agent.receipts import JobReceipt, RunReceipt
from mm_agent.types import Asset, ControlError, Job, LandingError
from tests.e2e.conftest import TOKEN
from tests.e2e.fake_control import FakeControlPlane
from tests.e2e.fake_landing import FakeLanding
from tests.fixtures import golden

JOB = Job(golden.START, golden.END, "ETH/USDT", "binance")
ASSET = Asset("ETHUSDT", "BINANCE_SPOT")


def client(control: FakeControlPlane, token: str = TOKEN) -> ControlPlane:
    return ControlPlane(control.url, token)


def test_check_in_is_recorded(control: FakeControlPlane) -> None:
    client(control).check_in("0.1.0", "cfg", None)
    assert len(control.check_ins) == 1
    assert control.check_ins[0]["config_hash"] == "cfg"


def test_bad_token_is_refused(control: FakeControlPlane) -> None:
    with pytest.raises(ControlError, match="HTTP 401"):
        client(control, "wrong").jobs()
    assert control.check_ins == []


def test_jobs_lists_what_was_seeded(control: FakeControlPlane) -> None:
    assert client(control).jobs() == []
    control.seed(*golden.jobs())
    assert client(control).jobs() == golden.jobs()


def test_upload_url_signs_a_path_the_landing_accepts(
    control: FakeControlPlane, landing: FakeLanding, tmp_path: Path
) -> None:
    with Writer(tmp_path, "orders.mapped") as writer:
        writer.write({"id": "o1"})
    landed = writer.close()
    url = client(control).upload_url(JOB, "orders.mapped")
    assert url.startswith(landing.url + "/")
    put(url, landed)
    key = f"golden/binance/ETH-USDT/{golden.START}-{golden.END}/orders.mapped.jsonl.gz"
    assert control.uploads == [
        {"from": JOB.start, "to": JOB.end, "pair": "ETH/USDT", "venue": "binance", "name": "orders.mapped"}
    ]
    stored = landing.objects[key]
    assert stored.content_type == "application/gzip"
    assert hashlib.sha256(gzip.decompress(stored.body)).hexdigest() == landed.sha256


def test_landing_refuses_unsigned_or_mis_signed_puts(landing: FakeLanding, tmp_path: Path) -> None:
    with Writer(tmp_path, "fills.raw") as writer:
        writer.write({"id": "f1"})
    landed = writer.close()
    with pytest.raises(LandingError, match="HTTP 403"):
        put(f"{landing.url}/golden/anything.jsonl.gz", landed)
    url = landing.sign("golden/fills.raw.jsonl.gz")
    with pytest.raises(LandingError, match="HTTP 403"):
        put(url[:-1] + "x", landed)
    assert landing.objects == {}
    assert requests.get(url, timeout=5).status_code == 405


def test_ok_receipt_completes_the_job(control: FakeControlPlane) -> None:
    control.seed(*golden.jobs())
    api = client(control)
    api.receipt(JobReceipt(JOB, ASSET, (), query_ms=3).wire())
    assert api.jobs() == [job for job in golden.jobs() if job != JOB]
    assert control.completed == [{"from": JOB.start, "to": JOB.end, "pair": "ETH/USDT", "venue": "binance"}]


def test_failed_receipt_leaves_the_job_outstanding(control: FakeControlPlane) -> None:
    control.seed(JOB)
    api = client(control)
    api.receipt(JobReceipt(JOB, None, (), query_ms=0, error="no alias").wire())
    api.receipt(RunReceipt("0.1.0", "cfg", None, 1, 2, 1, 0).wire())
    assert api.jobs() == [JOB]
    assert [r["kind"] for r in control.receipts] == ["job", "run"]
    assert control.completed == []


def test_off_contract_bodies_are_rejected(control: FakeControlPlane) -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    bad_check_in = requests.post(f"{control.url}/check_in", json={"version": "0.1.0"}, timeout=5)
    assert bad_check_in.status_code == 401
    bad_check_in = requests.post(f"{control.url}/check_in", json={"version": "0.1.0"}, headers=headers, timeout=5)
    assert bad_check_in.status_code == 400
    assert bad_check_in.json()["error"].startswith("off contract: ")
    bad_receipt = requests.post(f"{control.url}/receipt", json={"kind": "batch"}, headers=headers, timeout=5)
    assert bad_receipt.status_code == 400
    not_json = requests.post(f"{control.url}/receipt", data=b"{", headers=headers, timeout=5)
    assert not_json.status_code == 400
    assert control.check_ins == [] and control.receipts == []


def test_unknown_route_is_404(control: FakeControlPlane) -> None:
    with pytest.raises(ControlError, match="HTTP 404"):
        client(control)._get("/status")
