from typing import Any

import requests

from mm_agent.constants import TIMEOUT_SECONDS
from mm_agent.types import ControlError, Job
from mm_agent.utils import now_millis


class ControlPlane:
    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def check_in(self, version: str, config_hash: str, image_digest: str | None) -> None:
        self._post(
            "/check_in",
            {
                "version": version,
                "config_hash": config_hash,
                "image_digest": image_digest,
                "now": now_millis(),
            },
        )

    def jobs(self) -> list[Job]:
        body = self._get("/jobs")
        items = body.get("jobs")
        if not isinstance(items, list):
            raise ControlError(f"jobs: expected a list under 'jobs', got {type(items).__name__}")
        return [_job(item) for item in items]

    def upload_url(self, job: Job, name: str) -> str:
        body = self._post("/upload_url", {**job_wire(job), "name": name})
        url = body.get("url")
        if not isinstance(url, str) or not url:
            raise ControlError("upload_url: response has no 'url'")
        return url

    def receipt(self, payload: dict[str, Any]) -> None:
        self._post("/receipt", payload)

    def _get(self, path: str) -> dict[str, Any]:
        return self._call("GET", path, None)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", path, payload)

    def _call(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        try:
            response = self.session.request(method, self.url + path, json=payload, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as e:
            raise ControlError(f"{method} {path}: {e}") from e
        if response.status_code >= 400:
            raise ControlError(f"{method} {path}: HTTP {response.status_code}: {response.text[:200]}")
        if not response.content:
            return {}
        try:
            body = response.json()
        except ValueError as e:
            raise ControlError(f"{method} {path}: response is not JSON") from e
        if not isinstance(body, dict):
            raise ControlError(f"{method} {path}: expected a JSON object, got {type(body).__name__}")
        return body


def _job(item: Any) -> Job:
    if not isinstance(item, dict):
        raise ControlError(f"jobs: expected an object, got {type(item).__name__}")
    try:
        start, end, asset, venue, quote = item["from"], item["to"], item["asset"], item["venue"], item["quote"]
    except KeyError as e:
        raise ControlError(f"jobs: job is missing {e}") from e
    if not _millis(start) or not _millis(end) or start >= end:
        raise ControlError(f"jobs: from/to must be epoch ms with 0 <= from < to, got {start!r}, {end!r}")
    if not isinstance(asset, str):
        raise ControlError("jobs: asset must be a string")
    if not isinstance(venue, str | None) or not isinstance(quote, str | None):
        raise ControlError("jobs: venue and quote must be strings or null")
    return Job(start=start, end=end, asset=asset, venue=venue, quote=quote)


def _millis(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def job_wire(job: Job) -> dict[str, Any]:
    return {"from": job.start, "to": job.end, "asset": job.asset, "venue": job.venue, "quote": job.quote}
