from collections.abc import Iterator
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from mm_agent.constants import FETCH_ROWS
from mm_agent.types import DriverError, Job, Resolved, Source, Table
from mm_agent.utils import instant


def rows(source: Source, table: Table, job: Job, resolved: Resolved) -> Iterator[dict[str, Any]]:
    engine = _engine(source)
    statement = text(table.query).execution_options(stream_results=True, yield_per=FETCH_ROWS)
    try:
        with engine.connect() as connection:
            for row in connection.execute(statement, params(source, job, resolved)).mappings():
                yield dict(row)
    except SQLAlchemyError as e:
        raise DriverError(f"{source.kind.value} query failed: {e}") from e
    finally:
        engine.dispose()


def params(source: Source, job: Job, resolved: Resolved) -> dict[str, Any]:
    zone = ZoneInfo(source.timezone)
    start = instant(job.start)
    end = instant(job.end)
    return {
        "from": start.astimezone(zone).replace(tzinfo=None),
        "to": end.astimezone(zone).replace(tzinfo=None),
        "from_utc": start,
        "to_utc": end,
        "asset": resolved.asset,
    }


def _engine(source: Source) -> Engine:
    kind = source.kind.value
    try:
        return create_engine(source.dsn)
    except ImportError as e:
        raise DriverError(f"{kind} engine failed: {e}; install forgd-mm-agent[{kind}]") from e
    except SQLAlchemyError as e:
        raise DriverError(f"{kind} engine failed: {e}") from e
