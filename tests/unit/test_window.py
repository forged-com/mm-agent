import pytest

from mm_agent.types import Fill, Job, Order
from mm_agent.window import holds_fill, holds_order

NOON = 1_792_238_400_000
HOUR = 3_600_000
JOB = Job(start=NOON, end=NOON + HOUR, pair="ETH/USDT", venue="binance")


def order(placed: int, ended: int | None) -> Order:
    return Order(id="1", side="buy", price="1", qty="1", placed=placed, ended=ended)


def fill(ts: int) -> Fill:
    return Fill(order="1", ts=ts, price="1", qty="1", side="buy")


@pytest.mark.parametrize(
    ("placed", "ended", "held"),
    [
        (NOON - HOUR, None, True),
        (NOON - HOUR, NOON - 1, False),
        (NOON - HOUR, NOON, True),
        (NOON - HOUR, NOON + 1, True),
        (NOON, None, True),
        (NOON + HOUR - 1, None, True),
        (NOON + HOUR, None, False),
        (NOON + HOUR, NOON + 2 * HOUR, False),
        (NOON - 2 * HOUR, NOON + 2 * HOUR, True),
    ],
)
def test_holds_order(placed: int, ended: int | None, held: bool) -> None:
    assert holds_order(JOB, order(placed, ended)) is held


@pytest.mark.parametrize(
    ("ts", "held"),
    [(NOON - 1, False), (NOON, True), (NOON + HOUR - 1, True), (NOON + HOUR, False)],
)
def test_holds_fill(ts: int, held: bool) -> None:
    assert holds_fill(JOB, fill(ts)) is held
