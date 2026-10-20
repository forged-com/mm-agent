from dataclasses import dataclass
from typing import Any

from mm_agent.constants import RECEIPT_KIND_JOB, RECEIPT_KIND_RUN, STATUS_FAILED, STATUS_OK
from mm_agent.control import job_wire
from mm_agent.landing import Landed
from mm_agent.types import Asset, Job


@dataclass(frozen=True)
class JobReceipt:
    job: Job
    asset: Asset | None
    files: tuple[Landed, ...]
    query_ms: int
    warnings: tuple[str, ...] = ()
    error: str | None = None

    def wire(self) -> dict[str, Any]:
        return {
            "kind": RECEIPT_KIND_JOB,
            "status": STATUS_FAILED if self.error else STATUS_OK,
            "asset": {
                "pair": self.job.pair,
                "venue": self.job.venue,
                "source_pair": self.asset.pair if self.asset else None,
                "source_venue": self.asset.venue if self.asset else None,
            },
            "window": {"from": self.job.start, "to": self.job.end},
            "files": {f.name: {"rows": f.rows, "bytes": f.bytes, "sha256": f.sha256} for f in self.files},
            "query_ms": self.query_ms,
            "warnings": list(self.warnings),
            "error": self.error,
        }


@dataclass(frozen=True)
class RunError:
    job: Job | None
    error: str

    def wire(self) -> dict[str, Any]:
        return {"job": job_wire(self.job) if self.job else None, "error": self.error}


@dataclass(frozen=True)
class RunReceipt:
    version: str
    config_hash: str
    image_digest: str | None
    started: int
    ended: int
    attempted: int
    succeeded: int
    errors: tuple[RunError, ...] = ()

    @property
    def failed(self) -> int:
        return self.attempted - self.succeeded

    def wire(self) -> dict[str, Any]:
        return {
            "kind": RECEIPT_KIND_RUN,
            "version": self.version,
            "config_hash": self.config_hash,
            "image_digest": self.image_digest,
            "started": self.started,
            "ended": self.ended,
            "jobs": {"attempted": self.attempted, "succeeded": self.succeeded, "failed": self.failed},
            "errors": [e.wire() for e in self.errors],
        }
