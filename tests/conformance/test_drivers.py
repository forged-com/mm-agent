import os
from pathlib import Path

import pytest

from mm_agent.__main__ import land
from mm_agent.config import parse
from mm_agent.landing import Landed
from mm_agent.types import SourceKind
from tests.fixtures import golden, loaders

DSN_ENV = {
    SourceKind.POSTGRES: "MM_AGENT_CONFORM_POSTGRES_DSN",
    SourceKind.MYSQL: "MM_AGENT_CONFORM_MYSQL_DSN",
    SourceKind.CLICKHOUSE: "MM_AGENT_CONFORM_CLICKHOUSE_DSN",
}


def hashes(yaml: str) -> dict[str, dict[str, dict[str, object]]]:
    cfg = parse(yaml)
    result = {}
    for job in golden.jobs():
        files: list[Landed] = []
        receipt = land(cfg, job, files.append)
        assert receipt.error is None, receipt.error
        result[golden.key(job)] = {
            f.name: {"rows": f.rows, "sha256": f.sha256} for f in files if f.name in golden.MAPPED
        }
    return result


def server(kind: SourceKind) -> str:
    dsn = os.environ.get(DSN_ENV[kind])
    if not dsn:
        pytest.skip(f"{DSN_ENV[kind]} not set")
    return dsn


def source(kind: SourceKind, tmp_path: Path) -> str:
    if kind is SourceKind.POSTGRES:
        loaders.postgres(dsn := server(kind))
    elif kind is SourceKind.MYSQL:
        loaders.mysql(dsn := server(kind))
    elif kind is SourceKind.CLICKHOUSE:
        loaders.clickhouse(dsn := server(kind))
    elif kind is SourceKind.SQLITE:
        dsn = loaders.sqlite(tmp_path / "golden.db")
    else:
        loaders.files(tmp_path)
        dsn = str(tmp_path)
    return dsn


@pytest.mark.parametrize("kind", list(SourceKind))
def test_driver_reproduces_the_expected_hashes(kind: SourceKind, tmp_path: Path) -> None:
    assert hashes(golden.yaml(kind, source(kind, tmp_path))) == golden.expected()["files"]
