import gzip
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from mm_agent.constants import (
    CHECK_COLUMNS,
    CHECK_COUNTS,
    CHECK_ENDED,
    CHECK_FIELDS,
    CHECK_LANDS,
    CHECK_REFERENCES,
    CHECK_STABLE,
    CHECK_WINDOW,
    HOUR_MILLIS,
    MAPPED_SUFFIX,
    OPTIONAL_ORDER_FIELDS,
    VERDICT_FAIL,
    VERDICT_PASS,
)
from mm_agent.landing import Landed
from mm_agent.receipts import JobReceipt
from mm_agent.types import Config, Fill, Job, Order
from mm_agent.utils import now_millis
from mm_agent.window import holds_fill, holds_order


def windows(start: int, end: int) -> list[tuple[int, int]]:
    if end - start < HOUR_MILLIS:
        raise ValueError("--conform needs a window of at least an hour")
    hour = (start, start + HOUR_MILLIS)
    return [(start, end)] if hour == (start, end) else [(start, end), hour]


@dataclass(frozen=True)
class Check:
    name: str
    error: str | None = None
    note: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None


@dataclass
class Tally:
    rows: int = 0
    first: str = ""

    def add(self, example: str) -> None:
        if not self.rows:
            self.first = example
        self.rows += 1

    def text(self, what: str) -> str:
        return f"{self.rows} row(s) {what}, first {self.first}"


class Audit:
    def __init__(self, cfg: Config, job: Job) -> None:
        self.cfg = cfg
        self.job = job
        self.now = now_millis()
        self.orders: set[tuple[str, str, str]] = set()
        self.raw_rows = 0
        self.faults: dict[str, Tally] = {}

    def inspect(self, landed: Landed) -> None:
        if not landed.name.endswith(MAPPED_SUFFIX):
            self.raw_rows += landed.rows
        elif landed.name.startswith("orders"):
            self._orders(rows(landed))
        else:
            self._fills(rows(landed))

    def checks(self, first: JobReceipt, second: JobReceipt) -> list[Check]:
        if first.error:
            return [Check(CHECK_LANDS, first.error)]
        unmapped = sorted(OPTIONAL_ORDER_FIELDS - self.cfg.orders.map.keys())
        return [
            Check(CHECK_COLUMNS, note=f"verified on {self.raw_rows} raw row(s)"),
            Check(CHECK_FIELDS, note=f"optional {unmapped} not mapped" if unmapped else None),
            self._check(CHECK_WINDOW, "outside the window"),
            self._check(CHECK_ENDED, "with ended outside [placed, now]"),
            self._check(CHECK_REFERENCES, "referencing no order in the pull"),
            Check(CHECK_STABLE, _differs(first, second)),
            Check(CHECK_COUNTS, _empty(first)),
        ]

    def _orders(self, orders: Iterator[dict[str, Any]]) -> None:
        for row in orders:
            order = Order(**row)
            self.orders.add((order.venue, order.pair, order.id))
            example = f"order {order.id!r} placed={order.placed} ended={order.ended}"
            if not holds_order(self.job, order):
                self._fault(CHECK_WINDOW, example)
            if order.ended is not None and not order.placed <= order.ended <= self.now:
                self._fault(CHECK_ENDED, example)

    def _fills(self, fills: Iterator[dict[str, Any]]) -> None:
        for row in fills:
            fill = Fill(**{key: value for key, value in row.items() if key != "id"})
            example = f"fill of order {fill.order!r} ts={fill.ts}"
            if not holds_fill(self.job, fill):
                self._fault(CHECK_WINDOW, example)
            if (fill.venue, fill.pair, fill.order) not in self.orders:
                self._fault(CHECK_REFERENCES, example)

    def _fault(self, name: str, example: str) -> None:
        self.faults.setdefault(name, Tally()).add(example)

    def _check(self, name: str, what: str) -> Check:
        tally = self.faults.get(name)
        return Check(name) if tally is None else Check(name, tally.text(what))


@dataclass
class Gate:
    windows: int
    jobs: int = 0
    checks: list[Check] = field(default_factory=list)

    def record(self, checks: list[Check]) -> None:
        self.jobs += 1
        self.checks.extend(checks)

    @property
    def failed(self) -> int:
        return sum(not check.passed for check in self.checks)

    @property
    def passed(self) -> bool:
        return self.jobs > 0 and not self.failed

    def verdict(self) -> str:
        if self.passed:
            return (
                f"conformance {VERDICT_PASS}: {len(self.checks)} check(s) passed"
                f" over {self.jobs} job run(s) in {self.windows} window(s)"
            )
        if not self.jobs:
            return f"conformance {VERDICT_FAIL}: no jobs; configure at least one asset"
        return f"conformance {VERDICT_FAIL}: {self.failed} of {len(self.checks)} check(s) failed"


def rows(landed: Landed) -> Iterator[dict[str, Any]]:
    with gzip.open(landed.path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row: dict[str, Any] = json.loads(line)
            yield row


def _differs(first: JobReceipt, second: JobReceipt) -> str | None:
    if second.error:
        return f"second run failed: {second.error}"
    before = {f.name: (f.rows, f.sha256) for f in first.files}
    after = {f.name: (f.rows, f.sha256) for f in second.files}
    changed = [name for name in before if before[name] != after.get(name)]
    if not changed:
        return None
    name = changed[0]
    rows_before, sha_before = before[name]
    rows_after, sha_after = after.get(name, (0, "missing"))
    return (
        f"{', '.join(changed)} changed between runs;"
        f" {name}: {rows_before} row(s) sha256 {sha_before} then {rows_after} row(s) sha256 {sha_after}"
    )


def _empty(first: JobReceipt) -> str | None:
    empty = [f.name for f in first.files if f.name.endswith(MAPPED_SUFFIX) and not f.rows]
    return f"{', '.join(empty)}: 0 row(s)" if empty else None
