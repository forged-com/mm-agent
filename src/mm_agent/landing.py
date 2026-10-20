import gzip
import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, time
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import requests

from mm_agent.constants import GZIP_MTIME, LANDING_CONTENT_TYPE, LANDING_NAMES, LANDING_SUFFIX, TIMEOUT_SECONDS
from mm_agent.types import LandingError


@dataclass(frozen=True)
class Landed:
    name: str
    path: Path
    rows: int
    bytes: int
    sha256: str


class Writer:
    def __init__(self, directory: Path, name: str) -> None:
        if name not in LANDING_NAMES:
            raise LandingError(f"unknown landing name {name!r}; known are {sorted(LANDING_NAMES)}")
        self.name = name
        self.path = directory / f"{name}{LANDING_SUFFIX}"
        self.rows = 0
        self.digest = hashlib.sha256()
        self.landed: Landed | None = None
        try:
            self.handle = gzip.GzipFile(self.path, "wb", mtime=GZIP_MTIME)
        except OSError as e:
            raise LandingError(f"{self.path}: {e}") from e

    def write(self, row: Any) -> None:
        line = (json.dumps(row, default=_plain, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        try:
            self.handle.write(line)
        except OSError as e:
            raise LandingError(f"{self.path}: {e}") from e
        self.digest.update(line)
        self.rows += 1

    def close(self) -> Landed:
        if self.landed is None:
            try:
                self.handle.close()
                size = self.path.stat().st_size
            except OSError as e:
                raise LandingError(f"{self.path}: {e}") from e
            self.landed = Landed(self.name, self.path, self.rows, size, self.digest.hexdigest())
        return self.landed

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, value: BaseException | None, tb: TracebackType | None
    ) -> None:
        if value is None:
            self.close()
            return
        try:
            self.handle.close()
        except OSError:
            pass


def put(url: str, landed: Landed) -> None:
    try:
        with landed.path.open("rb") as handle:
            response = requests.put(
                url, data=handle, headers={"Content-Type": LANDING_CONTENT_TYPE}, timeout=TIMEOUT_SECONDS
            )
    except (OSError, requests.RequestException) as e:
        raise LandingError(f"PUT {landed.name}: {e}") from e
    if response.status_code >= 400:
        raise LandingError(f"PUT {landed.name}: HTTP {response.status_code}: {response.text[:200]}")


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)
