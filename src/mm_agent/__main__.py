import argparse
import hashlib
import logging
import os
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from mm_agent import __version__, config, conform, dryrun
from mm_agent.constants import (
    DEFAULT_CONFIG_PATH,
    IMAGE_DIGEST_ENV,
    JOB_ERRORS,
    LABEL_CONFORM,
    LABEL_DRY_RUN,
    LOG_FORMAT,
    MAPPED_SUFFIX,
    TICK_SECONDS,
    TOKEN_ENV,
)
from mm_agent.control import ControlPlane
from mm_agent.drivers import files, sql
from mm_agent.landing import Landed, Writer, put
from mm_agent.normalise import Normaliser, resolve
from mm_agent.receipts import JobReceipt, RunError, RunReceipt
from mm_agent.types import (
    Config,
    ConfigError,
    ControlError,
    Job,
    Resolved,
    SourceKind,
)
from mm_agent.utils import now_millis
from mm_agent.window import holds_fill, holds_order, in_target

log = logging.getLogger("mm_agent")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    parser = argparse.ArgumentParser(prog="forgd-mm-agent")
    parser.add_argument("config", nargs="?", type=Path, default=DEFAULT_CONFIG_PATH)
    offline = parser.add_mutually_exclusive_group()
    offline.add_argument("--dry-run", action="store_true")
    offline.add_argument("--conform", action="store_true")
    parser.add_argument("--from", dest="start", type=dryrun.moment, metavar="ISO-8601")
    parser.add_argument("--to", dest="end", type=dryrun.moment, metavar="ISO-8601")
    parser.add_argument("--day", type=dryrun.utc_day, metavar="YYYY-MM-DD")
    args = parser.parse_args(argv)
    mode = "--conform" if args.conform else "--dry-run" if args.dry_run else None
    try:
        window = dryrun.window(mode, args.start, args.end, args.day)
        windows = conform.windows(*window) if window and args.conform else []
    except ValueError as e:
        parser.error(str(e))
    token = os.environ.get(TOKEN_ENV, "")
    if window is None and not token:
        log.error("%s is not set", TOKEN_ENV)
        return 2
    try:
        cfg, config_hash = load(args.config)
    except ConfigError as e:
        log.error("%s", e)
        return 2
    image_digest = os.environ.get(IMAGE_DIGEST_ENV) or None
    if windows:
        return gate(cfg, config_hash, image_digest, windows)
    if window is not None:
        return dry_run(cfg, config_hash, image_digest, *window)
    control = ControlPlane(cfg.control.url, token)
    log.info("forgd-mm-agent %s for %s, config %s", __version__, cfg.mm, config_hash[:12])
    while True:
        try:
            run(cfg, control, config_hash, image_digest)
        except ControlError as e:
            log.error("run failed: %s", e)
        except Exception:
            log.exception("run crashed")
        time.sleep(TICK_SECONDS)


def load(path: Path) -> tuple[Config, str]:
    try:
        raw = path.read_bytes()
        text = raw.decode()
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    return config.parse(text), hashlib.sha256(raw).hexdigest()


def dry_run(cfg: Config, config_hash: str, image_digest: str | None, start: int, end: int) -> int:
    report = dryrun.Report(sys.stdout)
    errors = rehearse(cfg, config_hash, image_digest, start, end, report, LABEL_DRY_RUN, None)
    return 1 if errors else 0


def gate(cfg: Config, config_hash: str, image_digest: str | None, windows: list[tuple[int, int]]) -> int:
    report = dryrun.Report(sys.stdout)
    verdicts = conform.Gate(windows=len(windows))
    for start, end in windows:
        rehearse(cfg, config_hash, image_digest, start, end, report, LABEL_CONFORM, verdicts)
        report.line()
    report.line(verdicts.verdict())
    return 0 if verdicts.passed else 1


def rehearse(
    cfg: Config,
    config_hash: str,
    image_digest: str | None,
    start: int,
    end: int,
    report: dryrun.Report,
    label: str,
    verdicts: conform.Gate | None,
) -> list[RunError]:
    started = now_millis()
    jobs = dryrun.jobs(cfg, start, end)
    report.header(cfg, config_hash, label, start, end, len(jobs))
    errors: list[RunError] = []
    for job in jobs:
        report.job(cfg, job)
        audit = conform.Audit(cfg, job) if verdicts else None
        receipt = land(cfg, job, _tee(report.file, audit.inspect) if audit else report.file)
        report.receipt(receipt.wire(), indent="  ")
        if receipt.error:
            errors.append(RunError(job, receipt.error))
        if audit and verdicts:
            checks = audit.checks(receipt, land(cfg, job, _tee()))
            verdicts.record(checks)
            report.checks(checks)
    report.line()
    report.receipt(summarise(config_hash, image_digest, started, len(jobs), errors).wire())
    return errors


def _tee(*handlers: Callable[[Landed], None]) -> Callable[[Landed], None]:
    def deliver(file: Landed) -> None:
        for handler in handlers:
            handler(file)

    return deliver


def run(cfg: Config, control: ControlPlane, config_hash: str, image_digest: str | None) -> RunReceipt:
    started = now_millis()
    control.check_in(__version__, config_hash, image_digest)
    jobs = control.jobs()
    log.info("%d job(s) outstanding", len(jobs))
    errors = [RunError(job, error) for job in jobs if (error := process(cfg, control, job))]
    receipt = summarise(config_hash, image_digest, started, len(jobs), errors)
    control.receipt(receipt.wire())
    log.info(
        "run done: %d attempted, %d succeeded, %d failed", receipt.attempted, receipt.succeeded, receipt.failed
    )
    return receipt


def summarise(
    config_hash: str, image_digest: str | None, started: int, attempted: int, errors: list[RunError]
) -> RunReceipt:
    return RunReceipt(
        version=__version__,
        config_hash=config_hash,
        image_digest=image_digest,
        started=started,
        ended=now_millis(),
        attempted=attempted,
        succeeded=attempted - len(errors),
        errors=tuple(errors),
    )


def process(cfg: Config, control: ControlPlane, job: Job) -> str | None:
    receipt = land(cfg, job, lambda file: put(control.upload_url(job, file.name), file))
    if receipt.error:
        log.error("job %s [%d, %d): %s", job.label, job.start, job.end, receipt.error)
    else:
        rows = sum(f.rows for f in receipt.files if f.name.endswith(MAPPED_SUFFIX))
        log.info("job %s [%d, %d): %d rows in %d ms", job.label, job.start, job.end, rows, receipt.query_ms)
    try:
        control.receipt(receipt.wire())
    except ControlError as e:
        return f"{receipt.error}; {e}" if receipt.error else str(e)
    return receipt.error


@dataclass
class Progress:
    resolved: Resolved | None = None
    files: list[Landed] = field(default_factory=list)
    query_ms: int = 0

    def receipt(self, job: Job, error: str | None = None) -> JobReceipt:
        return JobReceipt(job, self.resolved, tuple(self.files), self.query_ms, error=error)


def land(cfg: Config, job: Job, deliver: Callable[[Landed], None]) -> JobReceipt:
    progress = Progress()
    try:
        _land(cfg, job, deliver, progress)
    except Exception as e:
        if isinstance(e, JOB_ERRORS):
            return progress.receipt(job, str(e))
        log.exception("job %s [%d, %d) crashed", job.label, job.start, job.end)
        return progress.receipt(job, f"{type(e).__name__}: {e}")
    return progress.receipt(job)


def _land(cfg: Config, job: Job, deliver: Callable[[Landed], None], progress: Progress) -> None:
    progress.resolved = resolve(cfg.aliases, job)
    clock = time.monotonic()
    with TemporaryDirectory() as directory:
        try:
            spooled = fetch(cfg, job, progress.resolved, Path(directory))
        finally:
            progress.query_ms = _elapsed_ms(clock)
        for file in spooled:
            deliver(file)
            progress.files.append(file)


def fetch(cfg: Config, job: Job, resolved: Resolved, directory: Path) -> tuple[Landed, ...]:
    normaliser = Normaliser(cfg)
    if cfg.source.kind is SourceKind.FILES:
        orders = files.rows(cfg.source, cfg.orders, job, resolved)
        fills = files.rows(cfg.source, cfg.fills, job, resolved)
        return (
            *spool(
                directory,
                "orders",
                orders,
                normaliser.order,
                lambda order: holds_order(job, order) and in_target(resolved, order),
            ),
            *spool(
                directory,
                "fills",
                fills,
                normaliser.fill,
                lambda fill: holds_fill(job, fill) and in_target(resolved, fill),
            ),
        )
    orders = sql.rows(cfg.source, cfg.orders, job, resolved)
    fills = sql.rows(cfg.source, cfg.fills, job, resolved)
    return (
        *spool(directory, "orders", orders, normaliser.order, lambda order: in_target(resolved, order)),
        *spool(directory, "fills", fills, normaliser.fill, lambda fill: in_target(resolved, fill)),
    )


def spool[T](
    directory: Path,
    table: str,
    rows: Iterator[dict[str, Any]],
    normalise: Callable[[dict[str, Any]], T],
    keep: Callable[[T], bool],
) -> tuple[Landed, Landed]:
    with Writer(directory, f"{table}.raw") as raw, Writer(directory, f"{table}.mapped") as mapped:
        for row in rows:
            record = normalise(row)
            if keep(record):
                raw.write(row)
                mapped.write(record)
    return raw.close(), mapped.close()


def _elapsed_ms(clock: float) -> int:
    return int((time.monotonic() - clock) * 1000)


if __name__ == "__main__":
    sys.exit(main())
