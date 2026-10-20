import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

VALID_YAML = """
mm: acme
control:
  url: https://mmcp.example.com/
source:
  kind: sqlite
  dsn: sqlite:///oms.db
aliases:
  venues: {binance: BINANCE_SPOT}
  pairs: {ETH/USDT: ETHUSDT}
orders:
  query: select * from orders
  map: {id: order_id, side: side, price: price, qty: orig_qty, placed: created_at, ended: closed_at}
fills:
  query: select * from fills
  map: {order: order_id, ts: exec_time, price: px, qty: qty, side: side}
"""


@pytest.fixture
def valid_yaml() -> str:
    return VALID_YAML


@dataclass
class Call:
    method: str
    path: str
    headers: dict[str, str]
    body: Any


@dataclass
class StubControlPlane:
    url: str
    calls: list[Call] = field(default_factory=list)
    responses: dict[str, tuple[int, bytes]] = field(default_factory=dict)

    def respond(self, path: str, status: int, body: Any = None, raw: bytes | None = None) -> None:
        payload = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
        self.responses[path] = (status, payload)


@pytest.fixture
def stub() -> Iterator[StubControlPlane]:
    state = StubControlPlane(url="")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            json_body = self.headers.get("Content-Type", "").startswith("application/json")
            body = json.loads(raw) if raw and json_body else raw or None
            state.calls.append(Call(self.command, self.path, dict(self.headers), body))
            status, payload = state.responses.get(self.path, (200, b""))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET: Callable[..., None] = _handle
        do_POST: Callable[..., None] = _handle
        do_PUT: Callable[..., None] = _handle

    server = HTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
