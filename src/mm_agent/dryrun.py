import gzip
import json
from datetime import UTC, date, datetime, timedelta
from itertools import islice
from typing import Any, TextIO

from mm_agent import __version__
from mm_agent.conform import Check
from mm_agent.constants import SAMPLE_ROWS, VERDICT_FAIL, VERDICT_PASS
from mm_agent.drivers import files, sql
from mm_agent.landing import Landed
from mm_agent.normalise import resolve
from mm_agent.types import Config, DriverError, Job, Resolved, SourceKind, Table
from mm_agent.utils import instant, millis


def moment(text: str) -> int:
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return millis(parsed)


def utc_day(text: str) -> tuple[int, int]:
    day = date.fromisoformat(text)
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return millis(start), millis(start + timedelta(days=1))


def window(
    offline: str | None, start: int | None, end: int | None, day: tuple[int, int] | None
) -> tuple[int, int] | None:
    if offline is None:
        if start is not None or end is not None or day is not None:
            raise ValueError("--from, --to and --day need --dry-run or --conform")
        return None
    if day is not None:
        if start is not None or end is not None:
            raise ValueError("--day cannot be combined with --from/--to")
        return day
    if start is None or end is None:
        raise ValueError(f"{offline} needs --from and --to, or --day")
    if start >= end:
        raise ValueError("--from must be before --to")
    return start, end


def jobs(cfg: Config, start: int, end: int) -> list[Job]:
    return [Job(start=start, end=end, asset=asset) for asset in sorted(cfg.aliases.assets)]


class Report:
    def __init__(self, out: TextIO) -> None:
        self.out = out

    def line(self, text: str = "") -> None:
        self.out.write(text + "\n")

    def header(self, cfg: Config, config_hash: str, label: str, start: int, end: int, count: int) -> None:
        self.line(f"forgd-mm-agent {__version__} {label} for {cfg.mm}")
        self.line(f"config sha256 {config_hash}")
        self.line(f"source {cfg.source.kind.value}, timezone {cfg.source.timezone}")
        self.line(f"window [{stamp(start)}, {stamp(end)}) = [{start}, {end})")
        self.line(f"{count} job(s): every configured asset, all venues and quotes")

    def job(self, cfg: Config, job: Job) -> None:
        resolved = resolve(cfg.aliases, job)
        self.line()
        self.line(f"job {job.label}")
        self.line(f"  asset {job.asset} -> {resolved.asset}")
        for name, table in (("orders", cfg.orders), ("fills", cfg.fills)):
            if cfg.source.kind is SourceKind.FILES:
                self._glob(cfg, name, table, job, resolved)
            else:
                self._query(cfg, name, table, job, resolved)

    def _query(self, cfg: Config, name: str, table: Table, job: Job, resolved: Resolved) -> None:
        self.line(f"  {name} query:")
        for text in table.query.strip().splitlines():
            self.line(f"    {text}")
        for key, value in sql.params(cfg.source, job, resolved).items():
            self.line(f"    :{key} = {bound(value, cfg.source.timezone)}")

    def _glob(self, cfg: Config, name: str, table: Table, job: Job, resolved: Resolved) -> None:
        try:
            glob = files.pattern(cfg.source, table, job, resolved)
            matched = files.files(cfg.source, table, job, resolved)
        except DriverError as e:
            self.line(f"  {name} files: {e}")
            return
        self.line(f"  {name} glob {glob!r} matches {len(matched)} file(s)")
        for path in matched:
            self.line(f"    {path}")

    def file(self, landed: Landed) -> None:
        self.line(f"  {landed.name}: {landed.rows} row(s), {landed.bytes} bytes, sha256 {landed.sha256}")
        with gzip.open(landed.path, "rt", encoding="utf-8") as handle:
            for text in islice(handle, SAMPLE_ROWS):
                self.line(f"    {text.rstrip()}")
        if landed.rows > SAMPLE_ROWS:
            self.line(f"    ... {landed.rows - SAMPLE_ROWS} more")

    def receipt(self, payload: dict[str, Any], indent: str = "") -> None:
        self.line(f"{indent}{payload['kind']} receipt (not sent):")
        for text in json.dumps(payload, indent=2).splitlines():
            self.line(f"{indent}  {text}")

    def checks(self, checks: list[Check]) -> None:
        self.line("  conformance:")
        for check in checks:
            verdict = VERDICT_PASS if check.passed else VERDICT_FAIL
            detail = f": {check.error}" if check.error else f" ({check.note})" if check.note else ""
            self.line(f"    {verdict} {check.name}{detail}")


def stamp(ms: int) -> str:
    return instant(ms).isoformat().replace("+00:00", "Z")


def bound(value: Any, timezone: str) -> str:
    if isinstance(value, datetime):
        return value.isoformat() if value.tzinfo else f"{value.isoformat()} ({timezone})"
    return repr(value)
