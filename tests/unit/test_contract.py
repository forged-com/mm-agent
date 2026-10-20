from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

from mm_agent.config import parse
from mm_agent.constants import DECIMAL_PATTERN
from mm_agent.control import ControlPlane, job_wire
from mm_agent.landing import Landed
from mm_agent.normalise import Normaliser
from mm_agent.receipts import JobReceipt, RunError, RunReceipt
from mm_agent.types import Asset, Job
from tests.conftest import StubControlPlane
from tests.contract import CONTRACT, NAMES, REGISTRY, SCHEMAS, check

NOON = 1_792_238_400_000
JOB = Job(start=NOON, end=NOON + 3_600_000, pair="ETH/USDT", venue="binance")
ASSET = Asset(pair="ETHUSDT", venue="BINANCE_SPOT")
LANDED = Landed("orders.raw", Path("/tmp/x/orders.raw.jsonl.gz"), 12, 345, "a" * 64)
ORDER_ROW = {
    "order_id": 42,
    "side": "BUY",
    "price": "1850.50",
    "orig_qty": 0.25,
    "created_at": NOON,
    "closed_at": None,
}
FILL_ROW = {"order_id": "42", "exec_time": NOON + 1000, "px": 1e-05, "qty": 1, "side": "S"}


def rejects(name: str, instance: Any, pointer: str = "") -> None:
    with pytest.raises(ValidationError):
        check(name, instance, pointer)


def _refs(node: Any) -> list[str]:
    if isinstance(node, dict):
        found = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
        return found + [ref for child in node.values() for ref in _refs(child)]
    if isinstance(node, list):
        return [ref for child in node for ref in _refs(child)]
    return []


@pytest.mark.parametrize("name", NAMES)
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    Draft202012Validator.check_schema(SCHEMAS[name])
    assert SCHEMAS[name]["$schema"] == "https://json-schema.org/draft/2020-12/schema"


@pytest.mark.parametrize("name", NAMES)
def test_every_ref_resolves(name: str) -> None:
    resolver = REGISTRY.resolver(base_uri=f"{name}.json")
    for ref in _refs(SCHEMAS[name]):
        assert resolver.lookup(ref).contents is not None


def test_contract_directory_holds_only_the_seven_shapes() -> None:
    assert sorted(p.name for p in CONTRACT.iterdir()) == sorted(f"{n}.json" for n in NAMES)


def test_check_in_payload(stub: StubControlPlane) -> None:
    ControlPlane(stub.url, "t").check_in("0.1.0", "cfg-hash", "sha256:abc")
    ControlPlane(stub.url, "t").check_in("0.1.0", "cfg-hash", None)
    for call in stub.calls:
        check("check_in", call.body)
    rejects("check_in", {"version": "0.1.0", "config_hash": "x", "now": NOON})
    rejects("check_in", {"version": "0.1.0", "config_hash": "x", "image_digest": None, "now": "soon"})


def test_job_wire() -> None:
    check("job", job_wire(JOB))
    rejects("job", {**job_wire(JOB), "extra": 1})
    rejects("job", {**job_wire(JOB), "from": str(NOON)})
    rejects("job", {**job_wire(JOB), "pair": ""})


def test_jobs_response_round_trips(stub: StubControlPlane) -> None:
    body = {"jobs": [job_wire(JOB), {**job_wire(JOB), "pair": "BTC/USDT"}]}
    check("jobs", body)
    check("jobs", {"jobs": []})
    stub.respond("/jobs", 200, body)
    assert ControlPlane(stub.url, "t").jobs() == [JOB, Job(JOB.start, JOB.end, "BTC/USDT", "binance")]
    rejects("jobs", {"jobs": [{"from": NOON, "to": NOON + 1}]})
    rejects("jobs", {})


def test_upload_url_request_and_response(stub: StubControlPlane) -> None:
    response = {"url": "https://put.example/signed?x=1"}
    stub.respond("/upload_url", 200, response)
    ControlPlane(stub.url, "t").upload_url(JOB, "fills.mapped")
    check("upload_url", stub.calls[0].body, "#/$defs/request")
    check("upload_url", response, "#/$defs/response")
    rejects("upload_url", {**job_wire(JOB), "name": "orders.jsonl"}, "#/$defs/request")
    rejects("upload_url", {"url": ""}, "#/$defs/response")
    rejects("upload_url", {}, "#/$defs/response")


def test_job_receipt_ok() -> None:
    check("receipt", JobReceipt(JOB, ASSET, (LANDED,), query_ms=42, warnings=("slow",)).wire())


def test_job_receipt_failed() -> None:
    check("receipt", JobReceipt(JOB, None, (), query_ms=0, error="no alias").wire())
    check("receipt", JobReceipt(JOB, ASSET, (LANDED,), query_ms=7, error="cursor died").wire())


def test_job_receipt_status_and_error_must_agree() -> None:
    rejects("receipt", {**JobReceipt(JOB, ASSET, (), query_ms=1).wire(), "error": "boom"})
    rejects("receipt", {**JobReceipt(JOB, ASSET, (), query_ms=1, error="boom").wire(), "error": None})


def test_job_receipt_rejects_bad_files() -> None:
    wire = JobReceipt(JOB, ASSET, (LANDED,), query_ms=1).wire()
    rejects("receipt", {**wire, "files": {"orders.jsonl": wire["files"]["orders.raw"]}})
    rejects("receipt", {**wire, "files": {"orders.raw": {**wire["files"]["orders.raw"], "sha256": "A" * 64}}})
    rejects("receipt", {**wire, "files": {"orders.raw": {"rows": 1, "bytes": 1}}})


def test_run_receipt() -> None:
    errors = (RunError(JOB, "cursor died"), RunError(None, "control plane unreachable"))
    receipt = RunReceipt("0.1.0", "cfg", "sha256:abc", NOON, NOON + 5000, 3, 1, errors)
    check("receipt", receipt.wire())
    check("receipt", RunReceipt("0.1.0", "cfg", None, NOON, NOON, 0, 0).wire())
    rejects("receipt", {**receipt.wire(), "errors": [{"job": None}]})
    rejects("receipt", {**receipt.wire(), "jobs": {"attempted": 3, "succeeded": 1}})


def test_receipt_rejects_unknown_kind() -> None:
    rejects("receipt", {**JobReceipt(JOB, ASSET, (), query_ms=1).wire(), "kind": "batch"})
    rejects("receipt", {})


def test_mapped_rows(valid_yaml: str) -> None:
    normaliser = Normaliser(parse(valid_yaml))
    order = asdict(normaliser.order(ORDER_ROW))
    fill = asdict(normaliser.fill(FILL_ROW))
    check("order", order)
    check("order", {**order, "ended": NOON + 1})
    check("fill", fill)
    assert fill["price"] == "1e-05"
    assert fill["id"] == normaliser.fill(FILL_ROW).id


@pytest.mark.parametrize(
    ("name", "patch"),
    [
        ("order", {"side": "BUY"}),
        ("order", {"price": "1,850"}),
        ("order", {"price": 1850.5}),
        ("order", {"placed": "2026-10-17T12:00:00Z"}),
        ("order", {"ended": "open"}),
        ("order", {"id": ""}),
        ("order", {"filled": 1.5}),
        ("order", {"remaining": "lots"}),
        ("fill", {"id": "abc"}),
        ("fill", {"side": "b"}),
        ("fill", {"ts": None}),
        ("fill", {"qty": "NaN"}),
    ],
)
def test_mapped_rows_reject(valid_yaml: str, name: str, patch: dict[str, Any]) -> None:
    normaliser = Normaliser(parse(valid_yaml))
    row = asdict(normaliser.order(ORDER_ROW) if name == "order" else normaliser.fill(FILL_ROW))
    rejects(name, {**row, **patch})
    rejects(name, {k: v for k, v in row.items() if k != next(iter(patch))})


def test_decimal_pattern_matches_the_contract() -> None:
    assert DECIMAL_PATTERN.pattern == SCHEMAS["order"]["$defs"]["decimal"]["pattern"]
