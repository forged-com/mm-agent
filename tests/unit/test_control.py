import pytest

from mm_agent.control import ControlPlane
from mm_agent.types import ControlError, Job
from tests.conftest import StubControlPlane

JOB = Job(start=1_760_702_400_000, end=1_760_706_000_000, asset="ETH", venue="binance")
URLS = {
    "urls": {
        name: "https://put.example/" + name
        for name in ("orders.raw", "orders.mapped", "fills.raw", "fills.mapped")
    }
}
JOB_WIRE = {"from": JOB.start, "to": JOB.end, "asset": "ETH", "venue": "binance", "quote": None}


def client(stub: StubControlPlane) -> ControlPlane:
    return ControlPlane(stub.url, "secret-token")


def test_bearer_token_on_every_call(stub: StubControlPlane) -> None:
    stub.respond("/jobs", 200, {"jobs": []})
    stub.respond("/upload_urls", 200, URLS)
    cp = client(stub)
    cp.check_in("0.1.0", "hash", None)
    cp.jobs()
    cp.upload_urls(JOB)
    cp.receipt({})
    assert [c.path for c in stub.calls] == ["/check_in", "/jobs", "/upload_urls", "/receipt"]
    assert all(c.headers["Authorization"] == "Bearer secret-token" for c in stub.calls)


def test_check_in_payload(stub: StubControlPlane) -> None:
    client(stub).check_in("0.1.0", "cfg-hash", "sha256:abc")
    call = stub.calls[0]
    assert call.method == "POST"
    assert call.body["version"] == "0.1.0"
    assert call.body["config_hash"] == "cfg-hash"
    assert call.body["image_digest"] == "sha256:abc"
    assert isinstance(call.body["now"], int)
    assert call.body["now"] > 1_700_000_000_000


def test_jobs_parsed(stub: StubControlPlane) -> None:
    stub.respond("/jobs", 200, {"jobs": [JOB_WIRE, {**JOB_WIRE, "asset": "BTC", "venue": None}]})
    jobs = client(stub).jobs()
    assert stub.calls[0].method == "GET"
    assert jobs == [JOB, Job(start=JOB.start, end=JOB.end, asset="BTC")]


def test_jobs_empty(stub: StubControlPlane) -> None:
    stub.respond("/jobs", 200, {"jobs": []})
    assert client(stub).jobs() == []


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({}, "expected a list under 'jobs'"),
        ({"jobs": {}}, "expected a list under 'jobs'"),
        ({"jobs": ["x"]}, "expected an object"),
        ({"jobs": [{"from": 1, "to": 2, "asset": "a", "quote": None}]}, "missing 'venue'"),
        ({"jobs": [{"from": 1, "to": 2, "asset": "a", "venue": None}]}, "missing 'quote'"),
        ({"jobs": [{"from": 1, "to": 2, "venue": None, "quote": None}]}, "missing 'asset'"),
        (
            {"jobs": [{"from": "1", "to": 2, "asset": "a", "venue": "v", "quote": None}]},
            "from/to must be epoch ms",
        ),
        ({"jobs": [{"from": 2, "to": 2, "asset": "a", "venue": "v", "quote": None}]}, "from < to"),
        (
            {"jobs": [{"from": False, "to": True, "asset": "a", "venue": "v", "quote": None}]},
            "from/to must be epoch ms",
        ),
        ({"jobs": [{"from": -1, "to": 2, "asset": "a", "venue": "v", "quote": None}]}, "0 <= from"),
        ({"jobs": [{"from": 1, "to": 2, "asset": 1, "venue": "v", "quote": None}]}, "asset must be a string"),
        ({"jobs": [{"from": 1, "to": 2, "asset": None, "venue": "v", "quote": None}]}, "asset must be a string"),
        (
            {"jobs": [{"from": 1, "to": 2, "asset": "a", "venue": 1, "quote": None}]},
            "venue and quote must be strings or null",
        ),
        (
            {"jobs": [{"from": 1, "to": 2, "asset": "a", "venue": None, "quote": 1}]},
            "venue and quote must be strings or null",
        ),
    ],
)
def test_jobs_rejects_bad_shape(stub: StubControlPlane, body: object, message: str) -> None:
    stub.respond("/jobs", 200, body)
    with pytest.raises(ControlError, match=message):
        client(stub).jobs()


def test_upload_urls_request_and_response(stub: StubControlPlane) -> None:
    stub.respond("/upload_urls", 200, URLS)
    assert client(stub).upload_urls(JOB) == URLS["urls"]
    assert stub.calls[0].body == JOB_WIRE


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"urls": []},
        {"urls": {"orders.raw": "https://put.example/x"}},
        {"urls": {**URLS["urls"], "fills.raw": ""}},
    ],
)
def test_upload_urls_requires_a_url_per_file(stub: StubControlPlane, body: object) -> None:
    stub.respond("/upload_urls", 200, body)
    with pytest.raises(ControlError, match="response needs a url for each of"):
        client(stub).upload_urls(JOB)


def test_receipt_posts_payload_verbatim(stub: StubControlPlane) -> None:
    payload = {"job": JOB_WIRE, "rows": 10, "errors": []}
    client(stub).receipt(payload)
    assert stub.calls[0].method == "POST"
    assert stub.calls[0].body == payload


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 503])
def test_http_error_raises(stub: StubControlPlane, status: int) -> None:
    stub.respond("/receipt", status, {"error": "nope"})
    with pytest.raises(ControlError, match=f"POST /receipt: HTTP {status}"):
        client(stub).receipt({})


def test_non_json_body_raises(stub: StubControlPlane) -> None:
    stub.respond("/jobs", 200, raw=b"<html>")
    with pytest.raises(ControlError, match="response is not JSON"):
        client(stub).jobs()


def test_non_object_body_raises(stub: StubControlPlane) -> None:
    stub.respond("/jobs", 200, [])
    with pytest.raises(ControlError, match="expected a JSON object, got list"):
        client(stub).jobs()


def test_unreachable_raises() -> None:
    with pytest.raises(ControlError, match="GET /jobs"):
        ControlPlane("http://127.0.0.1:9", "t").jobs()


def test_jobs_ignore_extra_keys(stub: StubControlPlane) -> None:
    stub.respond("/jobs", 200, {"jobs": [{**JOB_WIRE, "note": "ignored"}]})
    assert client(stub).jobs() == [JOB]


def test_jobs_null_list_rejected(stub: StubControlPlane) -> None:
    stub.respond("/jobs", 200, {"jobs": None})
    with pytest.raises(ControlError, match=r"expected a list under 'jobs', got NoneType"):
        client(stub).jobs()
