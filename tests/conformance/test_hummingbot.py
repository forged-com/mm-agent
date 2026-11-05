import gzip
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from mm_agent.__main__ import land
from mm_agent.config import parse
from mm_agent.landing import Landed
from mm_agent.types import Fill, Job, Order
from mm_agent.window import holds_fill, holds_order
from tests.fixtures import golden, loaders

EXAMPLE = Path(__file__).parents[2] / "examples" / "agent.hummingbot.yaml"
EXAMPLE_DSN = "sqlite:////data/hummingbot_trades.sqlite"
JOB = Job(golden.START, golden.END, "ETH")


@pytest.fixture
def mapped(tmp_path: Path) -> dict[str, list[dict[str, Any]]]:
    text = EXAMPLE.read_text()
    assert text.count(EXAMPLE_DSN) == 1
    cfg = parse(text.replace(EXAMPLE_DSN, loaders.hummingbot(tmp_path / "hummingbot_trades.sqlite")))
    rows: dict[str, list[dict[str, Any]]] = {}

    def deliver(file: Landed) -> None:
        with gzip.open(file.path, "rt", encoding="utf-8") as handle:
            rows[file.name] = [json.loads(line) for line in handle]

    receipt = land(cfg, JOB, deliver)
    assert receipt.error is None, receipt.error
    return rows


def golden_rows(table: str) -> list[dict[str, Any]]:
    return [row for row in getattr(golden, table)() if golden.asset_of(row) == JOB.asset]


def order_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return row["id"], row["side"], Decimal(row["price"]), Decimal(row["qty"]), row["placed"], row["ended"]


def fill_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return row["order"], row["ts"], Decimal(row["price"]), Decimal(row["qty"]), row["side"]


def test_example_lands_the_golden_orders(mapped: dict[str, list[dict[str, Any]]]) -> None:
    expected = [
        row
        for row in golden_rows("orders")
        if holds_order(JOB, Order(**{field: row[field] for field in golden.ORDER_FIELDS}))
    ]
    assert len(expected) == golden.expected()["files"][golden.key(JOB)]["orders.mapped"]["rows"]
    assert sorted(map(order_key, mapped["orders.mapped"])) == sorted(map(order_key, expected))
    assert all(row["filled"] is None and row["remaining"] is None for row in mapped["orders.mapped"])
    assert {(row["venue"], row["pair"]) for row in mapped["orders.mapped"]} == {("binance", "ETH-USDT")}
    assert len(mapped["orders.raw"]) == len(expected)


def test_example_lands_the_golden_fills(mapped: dict[str, list[dict[str, Any]]]) -> None:
    expected = [
        row
        for row in golden_rows("fills")
        if holds_fill(JOB, Fill(**{field: row[field] for field in golden.FILL_FIELDS}))
    ]
    assert len(expected) == golden.expected()["files"][golden.key(JOB)]["fills.mapped"]["rows"]
    assert sorted(map(fill_key, mapped["fills.mapped"])) == sorted(map(fill_key, expected))
    assert len(mapped["fills.raw"]) == len(expected)
    assert {(row["venue"], row["pair"]) for row in mapped["fills.mapped"]} == {("binance", "ETH-USDT")}
