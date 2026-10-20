import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

Reply = tuple[int, Any]


class Fake:
    def __init__(self) -> None:
        self.url = ""
        self._server: HTTPServer | None = None

    def respond(self, method: str, path: str, headers: dict[str, str], body: bytes) -> Reply:
        raise NotImplementedError

    def start(self) -> None:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b""
                status, reply = fake.respond(self.command, self.path, dict(self.headers), body)
                payload = b"" if reply is None else json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET: Callable[..., None] = _handle
            do_POST: Callable[..., None] = _handle
            do_PUT: Callable[..., None] = _handle

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
