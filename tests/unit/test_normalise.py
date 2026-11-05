import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from mm_agent.config import parse
from mm_agent.normalise import Normaliser, resolve
from mm_agent.types import Aliases, Fill, Job, NormaliseError, Order, Resolved

NOON = 1_792_238_400_000
ALIASES = Aliases(assets={"ETH": "ETH"}, venues={"binance": "BINANCE_SPOT"}, pairs={"ETH/USDT": "ETHUSDT"})
JOB = Job(start=NOON, end=NOON + 3_600_000, asset="ETH")

ORDER_ROW = {
    "order_id": 42,
    "venue": "BINANCE_SPOT",
    "symbol": "ETHUSDT",
    "side": "BUY",
    "price": Decimal("1850.50"),
    "orig_qty": "0.25",
    "created_at": NOON,
    "closed_at": None,
}
FILL_ROW = {
    "order_id": "42",
    "venue": "BINANCE_SPOT",
    "symbol": "ETHUSDT",
    "exec_time": NOON + 1000,
    "px": 1850.5,
    "qty": 1,
    "side": "S",
}


@pytest.fixture
def normaliser(valid_yaml: str) -> Normaliser:
    return Normaliser(parse(valid_yaml))


def with_timezone(valid_yaml: str, timezone: str) -> Normaliser:
    return Normaliser(
        parse(valid_yaml.replace("  dsn: sqlite:///oms.db", f"  dsn: sqlite:///oms.db\n  timezone: {timezone}"))
    )


def test_resolve_full_dump_needs_only_the_asset() -> None:
    assert resolve(Aliases(assets={"BTC": "XBT"}, venues={}, pairs={}), Job(0, 1, "BTC")) == Resolved("XBT")


def test_resolve_narrowed() -> None:
    assert resolve(ALIASES, Job(0, 1, "ETH", "binance", "USDT")) == Resolved("ETH", "BINANCE_SPOT", "ETHUSDT")
    assert resolve(ALIASES, Job(0, 1, "ETH", "binance")) == Resolved("ETH", "BINANCE_SPOT", None)
    assert resolve(ALIASES, Job(0, 1, "ETH", None, "USDT")) == Resolved("ETH", None, "ETHUSDT")


@pytest.mark.parametrize(
    ("job", "message"),
    [
        (Job(0, 1, "BTC"), "no alias for asset 'BTC'"),
        (Job(0, 1, "ETH", "okx"), "no alias for venue 'okx'"),
        (Job(0, 1, "ETH", None, "USDC"), "no alias for pair 'ETH/USDC'"),
    ],
)
def test_resolve_missing_alias(job: Job, message: str) -> None:
    with pytest.raises(NormaliseError, match=message):
        resolve(ALIASES, job)


def test_order_row(normaliser: Normaliser) -> None:
    assert normaliser.order(ORDER_ROW) == Order(
        id="42",
        venue="BINANCE_SPOT",
        pair="ETHUSDT",
        side="buy",
        price="1850.50",
        qty="0.25",
        placed=NOON,
        ended=None,
    )


def test_fill_row(normaliser: Normaliser) -> None:
    assert normaliser.fill(FILL_ROW) == Fill(
        venue="BINANCE_SPOT", pair="ETHUSDT", order="42", ts=NOON + 1000, price="1850.5", qty="1", side="sell"
    )


def with_progress(valid_yaml: str) -> Normaliser:
    return Normaliser(
        parse(valid_yaml.replace("ended: closed_at}", "ended: closed_at, filled: done, remaining: left}"))
    )


def test_unmapped_filled_and_remaining_are_null(normaliser: Normaliser) -> None:
    order = normaliser.order({**ORDER_ROW, "done": "0.1", "left": "0.15"})
    assert (order.filled, order.remaining) == (None, None)


def test_mapped_filled_and_remaining(valid_yaml: str) -> None:
    order = with_progress(valid_yaml).order({**ORDER_ROW, "done": Decimal("0.10"), "left": 0.15})
    assert (order.filled, order.remaining) == ("0.10", "0.15")


@pytest.mark.parametrize("value", [None, "", "  "])
def test_mapped_filled_and_remaining_accept_null(valid_yaml: str, value: object) -> None:
    order = with_progress(valid_yaml).order({**ORDER_ROW, "done": value, "left": value})
    assert (order.filled, order.remaining) == (None, None)


def test_mapped_filled_must_be_a_decimal(valid_yaml: str) -> None:
    with pytest.raises(NormaliseError, match=r"orders\.filled \(done\): 'some' is not a decimal number"):
        with_progress(valid_yaml).order({**ORDER_ROW, "done": "some", "left": "1"})


def test_mapped_filled_column_must_exist(valid_yaml: str) -> None:
    with pytest.raises(NormaliseError, match=r"orders\.filled: row has no column 'done'"):
        with_progress(valid_yaml).order({**ORDER_ROW, "left": "1"})


def test_fill_id_is_the_sha256_of_the_mapped_row(normaliser: Normaliser) -> None:
    fill = normaliser.fill(FILL_ROW)
    assert (
        fill.id == hashlib.sha256(b'["BINANCE_SPOT","ETHUSDT","42",1792238401000,"1850.5","1","sell"]').hexdigest()
    )
    assert fill.id == normaliser.fill({**FILL_ROW, "qty": "1", "side": "sell"}).id
    assert fill.id != normaliser.fill({**FILL_ROW, "qty": 2}).id
    assert fill.id != normaliser.fill({**FILL_ROW, "venue": "OKX"}).id
    assert fill.id != normaliser.fill({**FILL_ROW, "symbol": "ETHUSDC"}).id


def test_extra_columns_ignored(normaliser: Normaliser) -> None:
    assert normaliser.order({**ORDER_ROW, "exchange": "x", "client_tag": 7}).id == "42"


def test_missing_column(normaliser: Normaliser) -> None:
    row = dict(ORDER_ROW)
    del row["orig_qty"]
    with pytest.raises(NormaliseError, match=r"orders\.qty: row has no column 'orig_qty'"):
        normaliser.order(row)


@pytest.mark.parametrize("field", ["order_id", "venue", "symbol", "side", "price", "orig_qty", "created_at"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_required_fields_reject_null(normaliser: Normaliser, field: str, value: object) -> None:
    with pytest.raises(NormaliseError, match="is null"):
        normaliser.order({**ORDER_ROW, field: value})


@pytest.mark.parametrize("value", [None, "", "  "])
def test_ended_null(normaliser: Normaliser, value: object) -> None:
    assert normaliser.order({**ORDER_ROW, "closed_at": value}).ended is None


def test_ended_set(normaliser: Normaliser) -> None:
    assert normaliser.order({**ORDER_ROW, "closed_at": NOON + 5}).ended == NOON + 5


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (NOON // 1000, NOON),
        (NOON, NOON),
        (NOON * 1000, NOON),
        (NOON / 1000 + 0.5, NOON + 500),
        (float(NOON), NOON),
        (NOON * 1000 + 499, NOON),
        (str(NOON // 1000), NOON),
        (str(NOON), NOON),
        (str(NOON * 1000), NOON),
        (f"{NOON / 1000:.3f}", NOON),
        ("2026-10-17T12:00:00Z", NOON),
        ("2026-10-17T12:00:00+00:00", NOON),
        ("2026-10-17T14:00:00+02:00", NOON),
        ("2026-10-17 12:00:00", NOON),
        ("2026-10-17T12:00:00.250", NOON + 250),
        ("2026-10-17T12:00:00.250500", NOON + 250),
        (datetime(2026, 10, 17, 12, tzinfo=UTC), NOON),
        (datetime(2026, 10, 17, 14, tzinfo=ZoneInfo("Europe/Berlin")), NOON),
        (datetime.fromisoformat("2026-10-17T12:00:00"), NOON),
    ],
)
def test_timestamps_in_utc(normaliser: Normaliser, value: object, expected: int) -> None:
    assert normaliser.order({**ORDER_ROW, "created_at": value}).placed == expected


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-17 12:00:00",
        datetime.fromisoformat("2026-10-17T12:00:00"),
    ],
)
def test_naive_timestamps_use_source_timezone(valid_yaml: str, value: object) -> None:
    normaliser = with_timezone(valid_yaml, "America/New_York")
    assert normaliser.order({**ORDER_ROW, "created_at": value}).placed == NOON + 4 * 3_600_000


def test_aware_timestamps_ignore_source_timezone(valid_yaml: str) -> None:
    normaliser = with_timezone(valid_yaml, "America/New_York")
    assert normaliser.order({**ORDER_ROW, "created_at": "2026-10-17T12:00:00Z"}).placed == NOON


def test_epoch_strings_ignore_source_timezone(valid_yaml: str) -> None:
    normaliser = with_timezone(valid_yaml, "America/New_York")
    assert normaliser.order({**ORDER_ROW, "created_at": str(NOON)}).placed == NOON


@pytest.mark.parametrize(
    "value", ["yesterday", "2026-13-01", "12:00", -1, -5.0, float("nan"), float("inf"), True, b"x"]
)
def test_bad_timestamps(normaliser: Normaliser, value: object) -> None:
    with pytest.raises(NormaliseError, match=r"orders\.placed"):
        normaliser.order({**ORDER_ROW, "created_at": value})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("BUY", "buy"),
        ("buy", "buy"),
        (" Buy ", "buy"),
        ("B", "buy"),
        ("bid", "buy"),
        ("1", "buy"),
        (1, "buy"),
        ("SELL", "sell"),
        ("s", "sell"),
        ("ASK", "sell"),
        ("-1", "sell"),
        (-1, "sell"),
    ],
)
def test_sides(normaliser: Normaliser, value: object, expected: str) -> None:
    assert normaliser.order({**ORDER_ROW, "side": value}).side == expected


@pytest.mark.parametrize("value", ["long", "0", 2, "buy sell", True])
def test_bad_sides(normaliser: Normaliser, value: object) -> None:
    with pytest.raises(NormaliseError, match="unknown side"):
        normaliser.order({**ORDER_ROW, "side": value})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1850.50"), "1850.50"),
        (Decimal("0.00000001"), "1E-8"),
        ("1850.50", "1850.50"),
        (" 0.25 ", "0.25"),
        ("1e-8", "1e-8"),
        (1850, "1850"),
        (0, "0"),
        (1850.5, "1850.5"),
        (0.1, "0.1"),
        (1e-05, "1e-05"),
    ],
)
def test_decimals(normaliser: Normaliser, value: object, expected: str) -> None:
    assert normaliser.order({**ORDER_ROW, "price": value}).price == expected


@pytest.mark.parametrize(
    "value",
    [
        "abc",
        "1.2.3",
        "NaN",
        "Infinity",
        "1_000",
        "\u0661\u0662\u0663",
        "1,5",
        float("nan"),
        float("inf"),
        True,
        [1],
    ],
)
def test_bad_decimals(normaliser: Normaliser, value: object) -> None:
    with pytest.raises(NormaliseError, match=r"orders\.price"):
        normaliser.order({**ORDER_ROW, "price": value})


@pytest.mark.parametrize(("value", "expected"), [("abc-123", "abc-123"), (" 42 ", "42"), (42, "42")])
def test_ids(normaliser: Normaliser, value: object, expected: str) -> None:
    assert normaliser.order({**ORDER_ROW, "order_id": value}).id == expected
    assert normaliser.fill({**FILL_ROW, "order_id": value}).order == expected


@pytest.mark.parametrize("value", [1.5, True])
def test_bad_ids(normaliser: Normaliser, value: object) -> None:
    with pytest.raises(NormaliseError, match=r"orders\.id .*: expected text"):
        normaliser.order({**ORDER_ROW, "order_id": value})


def test_error_names_field_and_column(normaliser: Normaliser) -> None:
    with pytest.raises(NormaliseError, match=r"^fills\.ts \(exec_time\): "):
        normaliser.fill({**FILL_ROW, "exec_time": "nope"})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (10**11 - 1, (10**11 - 1) * 1000),
        (10**11, 10**11),
        (10**14 - 1, 10**14 - 1),
        (10**14, 10**11),
        ("1.7922384e12", NOON),
        (" 1792238400 ", NOON),
        ("2026-10-17", NOON - 12 * 3_600_000),
        ("20261017", 20_261_017_000),
    ],
)
def test_timestamp_edges(normaliser: Normaliser, value: object, expected: int) -> None:
    assert normaliser.order({**ORDER_ROW, "created_at": value}).placed == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-25 02:30:00", 1_792_888_200_000),
        ("2026-03-29 02:30:00", 1_774_747_800_000),
    ],
)
def test_naive_dst_fold_and_gap_use_the_pre_transition_offset(valid_yaml: str, value: str, expected: int) -> None:
    normaliser = with_timezone(valid_yaml, "Europe/Berlin")
    assert normaliser.order({**ORDER_ROW, "created_at": value}).placed == expected


@pytest.mark.parametrize("column", ["order_id", "venue", "symbol", "exec_time", "px", "qty", "side"])
def test_fill_fields_reject_null(normaliser: Normaliser, column: str) -> None:
    with pytest.raises(NormaliseError, match=rf"fills\.\w+ \({column}\): is null"):
        normaliser.fill({**FILL_ROW, column: None})


@pytest.mark.parametrize("value", [1.0, -1.0, "+1", "BUY SELL", 0])
def test_more_bad_sides(normaliser: Normaliser, value: object) -> None:
    with pytest.raises(NormaliseError, match="unknown side"):
        normaliser.fill({**FILL_ROW, "side": value})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("-1.5", "-1.5"),
        ("+1", "+1"),
        (".5", ".5"),
        ("5.", "5."),
        ("1E+3", "1E+3"),
        (Decimal("1E+3"), "1E+3"),
        (Decimal("-0"), "-0"),
        (10**30, str(10**30)),
        (-7, "-7"),
        (1e21, "1e+21"),
    ],
)
def test_decimal_edges(normaliser: Normaliser, value: object, expected: str) -> None:
    assert normaliser.fill({**FILL_ROW, "qty": value}).qty == expected


@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), "1e", "e5", "--1", "1.5 2", "0x10", b"1"],
)
def test_bad_decimal_edges(normaliser: Normaliser, value: object) -> None:
    with pytest.raises(NormaliseError, match=r"fills\.qty \(qty\)"):
        normaliser.fill({**FILL_ROW, "qty": value})


@pytest.mark.parametrize(("value", "expected"), [(0, "0"), (-1, "-1"), (Decimal(42), "42"), ("a b", "a b")])
def test_id_edges(normaliser: Normaliser, value: object, expected: str) -> None:
    assert normaliser.order({**ORDER_ROW, "order_id": value}).id == expected
