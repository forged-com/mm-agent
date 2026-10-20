import secrets
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from tests.e2e.serving import Fake, Reply

SIGNATURE = "sig"


@dataclass(frozen=True)
class Stored:
    body: bytes
    content_type: str


class FakeLanding(Fake):
    def __init__(self) -> None:
        super().__init__()
        self.signatures: dict[str, str] = {}
        self.objects: dict[str, Stored] = {}
        self.expired: set[str] = set()

    def sign(self, path: str) -> str:
        self.signatures[path] = secrets.token_hex(8)
        return f"{self.url}/{path}?{SIGNATURE}={self.signatures[path]}"

    def expire(self, path: str) -> None:
        self.expired.add(path)

    def respond(self, method: str, path: str, headers: dict[str, str], body: bytes) -> Reply:
        if method != "PUT":
            return 405, {"error": f"{method} not allowed"}
        parts = urlsplit(path)
        key = parts.path.lstrip("/")
        signature = parse_qs(parts.query).get(SIGNATURE, [""])[0]
        if key not in self.signatures or signature != self.signatures[key]:
            return 403, {"error": "unsigned or unknown object"}
        if key in self.expired:
            return 403, {"error": "signature expired"}
        self.objects[key] = Stored(body, headers.get("Content-Type", ""))
        return 200, None
