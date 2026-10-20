import csv
import gzip
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO
from zoneinfo import ZoneInfo

from mm_agent.constants import CSV_SUFFIX, DATE_FORMAT, FILE_ENCODING, GZIP_SUFFIX, JSONL_SUFFIX
from mm_agent.types import Asset, DriverError, Job, Source, Table
from mm_agent.utils import instant


def rows(source: Source, table: Table, job: Job, asset: Asset) -> Iterator[dict[str, Any]]:
    for path in files(source, table, job, asset):
        yield from _read(path)


def files(source: Source, table: Table, job: Job, asset: Asset) -> list[Path]:
    root = Path(source.dsn)
    if not root.is_dir():
        raise DriverError(f"files root {source.dsn!r} is not a directory")
    return sorted(path for path in root.glob(pattern(source, table, job, asset)) if path.is_file())


def pattern(source: Source, table: Table, job: Job, asset: Asset) -> str:
    zone = ZoneInfo(source.timezone)
    placeholders = {
        "pair": asset.pair,
        "venue": asset.venue,
        "from": instant(job.start).astimezone(zone).strftime(DATE_FORMAT),
        "to": instant(job.end).astimezone(zone).strftime(DATE_FORMAT),
    }
    try:
        return table.query.format_map(placeholders)
    except KeyError as e:
        raise DriverError(
            f"glob {table.query!r}: unknown placeholder {{{e.args[0]}}}; known are {sorted(placeholders)}"
        ) from e
    except (IndexError, ValueError) as e:
        raise DriverError(f"glob {table.query!r}: {e}") from e


def _read(path: Path) -> Iterator[dict[str, Any]]:
    compressed = path.name.endswith(GZIP_SUFFIX)
    stem = path.name.removesuffix(GZIP_SUFFIX)
    if stem.endswith(CSV_SUFFIX):
        reader = _csv
    elif stem.endswith(JSONL_SUFFIX):
        reader = _jsonl
    else:
        raise DriverError(
            f"{path}: unsupported file type; expected {CSV_SUFFIX} or {JSONL_SUFFIX}, optionally {GZIP_SUFFIX}"
        )
    try:
        with _open(path, compressed) as handle:
            yield from reader(handle, path)
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        raise DriverError(f"{path}: {e}") from e


def _open(path: Path, compressed: bool) -> TextIO:
    if compressed:
        return gzip.open(path, "rt", encoding=FILE_ENCODING, newline="")
    return path.open(encoding=FILE_ENCODING, newline="")


def _csv(handle: TextIO, path: Path) -> Iterator[dict[str, Any]]:
    reader = csv.DictReader(handle)
    for row in reader:
        if None in row:
            raise DriverError(f"{path}:{reader.line_num}: more values than header columns")
        yield dict(row)


def _jsonl(handle: TextIO, path: Path) -> Iterator[dict[str, Any]]:
    for number, line in enumerate(handle, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise DriverError(f"{path}:{number}: {e}") from e
        if not isinstance(row, dict):
            raise DriverError(f"{path}:{number}: expected an object, got {type(row).__name__}")
        yield row
