import pytest

from mm_agent.types import Fill, Job, Order, Resolved
from mm_agent.window import holds_fill, holds_order, in_target

NOON = 1_792_238_400_000
HOUR = 3_600_000
JOB = Job(start=NOON, end=NOON + HOUR, asset="ETH")


def order(placed: int, ended: int | None) -> Order:
    return Order(id="1", venue="V", pair="P", side="buy", price="1", qty="1", placed=placed, ended=ended)


def fill(ts: int) -> Fill:
    return Fill(venue="V", pair="P", order="1", ts=ts, price="1", qty="1", side="buy")


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


@pytest.mark.parametrize(
    ("venue", "pair", "held"),
    [
        (None, None, True),
        ("V", None, True),
        (None, "P", True),
        ("V", "P", True),
        ("OTHER", None, False),
        (None, "OTHER", False),
        ("V", "OTHER", False),
        ("OTHER", "P", False),
    ],
)
def test_in_target(venue: str | None, pair: str | None, held: bool) -> None:
    resolved = Resolved("ETH", venue, (pair,) if pair else None)
    assert in_target(resolved, order(NOON, None)) is held
    assert in_target(resolved, fill(NOON)) is held


def test_in_target_matches_any_pair_string() -> None:
    assert in_target(Resolved("ETH", None, ("OTHER", "P")), order(NOON, None))
    assert not in_target(Resolved("ETH", None, ("OTHER", "ANOTHER")), order(NOON, None))
