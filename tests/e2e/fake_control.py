import json
from typing import Any

from jsonschema import ValidationError

from mm_agent.control import job_wire
from mm_agent.types import Job
from tests.contract import check
from tests.e2e.fake_landing import FakeLanding
from tests.e2e.serving import Fake, Reply

OBJECT_SUFFIX = ".jsonl.gz"
NAMES = ("orders.raw", "orders.mapped", "fills.raw", "fills.mapped")


class FakeControlPlane(Fake):
    def __init__(self, token: str, mm: str, landing: FakeLanding) -> None:
        super().__init__()
        self.token = token
        self.mm = mm
        self.landing = landing
        self.outstanding: list[dict[str, Any]] = []
        self.completed: list[dict[str, Any]] = []
        self.check_ins: list[dict[str, Any]] = []
        self.uploads: list[dict[str, Any]] = []
        self.receipts: list[dict[str, Any]] = []

    def seed(self, *jobs: Job) -> None:
        self.outstanding.extend(job_wire(job) for job in jobs)

    def done(self, *jobs: Job) -> None:
        self.completed.extend(job_wire(job) for job in jobs)

    def object_path(self, request: dict[str, Any], name: str) -> str:
        target = f"{request['asset']}/{request['venue'] or 'all'}/{request['quote'] or 'all'}"
        window = f"{request['from']}-{request['to']}"
        return f"{self.mm}/{target}/{window}/{name}{OBJECT_SUFFIX}"

    def respond(self, method: str, path: str, headers: dict[str, str], body: bytes) -> Reply:
        if headers.get("Authorization") != f"Bearer {self.token}":
            return 401, {"error": "bad token"}
        try:
            payload = json.loads(body) if body else None
        except ValueError:
            return 400, {"error": "body is not JSON"}
        try:
            return self._route(method, path, payload)
        except ValidationError as e:
            return 400, {"error": f"off contract: {e.message}"}

    def _route(self, method: str, path: str, payload: Any) -> Reply:
        match method, path:
            case "POST", "/check_in":
                check("check_in", payload)
                self.check_ins.append(payload)
                return 200, None
            case "GET", "/jobs":
                return 200, {"jobs": list(self.outstanding)}
            case "POST", "/upload_urls":
                check("upload_urls", payload, "#/$defs/request")
                if payload in self.completed:
                    return 409, {"error": "job already done"}
                self.uploads.append(payload)
                return 200, {"urls": {name: self.landing.sign(self.object_path(payload, name)) for name in NAMES}}
            case "POST", "/receipt":
                check("receipt", payload)
                self.receipts.append(payload)
                if payload["kind"] == "job" and payload["status"] == "ok":
                    self._complete(payload)
                return 200, None
        return 404, {"error": f"no route for {method} {path}"}

    def _complete(self, receipt: dict[str, Any]) -> None:
        target = receipt["target"]
        done = {**receipt["window"], "asset": target["asset"], "venue": target["venue"], "quote": target["quote"]}
        self.outstanding = [job for job in self.outstanding if job != done]
        self.completed.append(done)
