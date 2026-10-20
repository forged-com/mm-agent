import gzip
import hashlib
from pathlib import Path

import pytest

from mm_agent import __version__
from mm_agent.__main__ import load, run
from mm_agent.constants import LANDING_CONTENT_TYPE
from mm_agent.control import ControlPlane, job_wire
from mm_agent.types import Config, Job, SourceKind
from tests.conformance.test_drivers import source
from tests.e2e.conftest import TOKEN
from tests.e2e.fake_control import FakeControlPlane
from tests.e2e.fake_landing import FakeLanding
from tests.fixtures import golden

IMAGE_DIGEST = "sha256:e2e"
NAMES = ("orders.raw", "orders.mapped", "fills.raw", "fills.mapped")
OFFLINE = (SourceKind.SQLITE, SourceKind.FILES)


def configured(
    kind: SourceKind, control: FakeControlPlane, tmp_path: Path, *edits: tuple[str, str]
) -> tuple[Config, str]:
    yaml = golden.yaml(kind, source(kind, tmp_path)).replace(golden.CONTROL_URL, control.url)
    for old, new in edits:
        yaml = yaml.replace(old, new)
    path = tmp_path / "agent.yaml"
    path.write_text(yaml)
    return load(path)


def object_key(job: Job, name: str) -> str:
    pair = job.pair.replace("/", "-")
    return f"{golden.MM}/{job.venue}/{pair}/{job.start}-{job.end}/{name}.jsonl.gz"


@pytest.mark.parametrize("kind", OFFLINE)
def test_one_tick_lands_every_golden_job(
    kind: SourceKind, control: FakeControlPlane, landing: FakeLanding, tmp_path: Path
) -> None:
    cfg, config_hash = configured(kind, control, tmp_path)
    control.seed(*golden.jobs())
    jobs = golden.jobs()

    receipt = run(cfg, ControlPlane(cfg.control.url, TOKEN), config_hash, IMAGE_DIGEST)

    assert len(control.check_ins) == 1
    check_in = control.check_ins[0]
    assert (check_in["version"], check_in["config_hash"], check_in["image_digest"]) == (
        __version__,
        config_hash,
        IMAGE_DIGEST,
    )
    assert receipt.started <= check_in["now"] <= receipt.ended

    assert control.uploads == [{**job_wire(job), "name": name} for job in jobs for name in NAMES]
    assert sorted(landing.objects) == sorted(object_key(job, name) for job in jobs for name in NAMES)

    expected = golden.expected()["files"]
    for job in jobs:
        for name in golden.MAPPED:
            stored = landing.objects[object_key(job, name)]
            assert stored.content_type == LANDING_CONTENT_TYPE
            body = gzip.decompress(stored.body)
            assert hashlib.sha256(body).hexdigest() == expected[golden.key(job)][name]["sha256"]
            assert body.count(b"\n") == expected[golden.key(job)][name]["rows"]

    assert [r["kind"] for r in control.receipts] == ["job", "job", "run"]
    for job, job_receipt in zip(jobs, control.receipts[:2], strict=True):
        assert job_receipt["status"] == "ok" and job_receipt["error"] is None
        assert job_receipt["window"] == {"from": job.start, "to": job.end}
        assert job_receipt["asset"] == {
            "pair": job.pair,
            "venue": job.venue,
            "source_pair": golden.PAIRS[job.pair],
            "source_venue": golden.VENUES[job.venue],
        }
        assert sorted(job_receipt["files"]) == sorted(NAMES)
        for name, file in job_receipt["files"].items():
            stored = landing.objects[object_key(job, name)]
            assert file["bytes"] == len(stored.body)
            assert file["sha256"] == hashlib.sha256(gzip.decompress(stored.body)).hexdigest()
            assert file["rows"] == job_receipt["files"][f"{name.split('.')[0]}.mapped"]["rows"]

    run_receipt = control.receipts[2]
    assert run_receipt == receipt.wire()
    assert run_receipt["jobs"] == {"attempted": 2, "succeeded": 2, "failed": 0}
    assert run_receipt["errors"] == []
    assert (run_receipt["version"], run_receipt["config_hash"], run_receipt["image_digest"]) == (
        __version__,
        config_hash,
        IMAGE_DIGEST,
    )

    assert control.outstanding == []
    assert control.completed == [job_wire(job) for job in jobs]


def test_a_quiet_tick_only_checks_in(control: FakeControlPlane, landing: FakeLanding, tmp_path: Path) -> None:
    cfg, config_hash = configured(SourceKind.SQLITE, control, tmp_path)
    receipt = run(cfg, ControlPlane(cfg.control.url, TOKEN), config_hash, None)
    assert receipt.attempted == 0 and receipt.errors == ()
    assert len(control.check_ins) == 1
    assert control.uploads == [] and landing.objects == {}
    assert [r["kind"] for r in control.receipts] == ["run"]
    assert control.receipts[0]["jobs"] == {"attempted": 0, "succeeded": 0, "failed": 0}
