import math
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from mm_agent.constants import BUY_SIDES, DECIMAL_PATTERN, EPOCH_MILLIS_BELOW, EPOCH_SECONDS_BELOW, SELL_SIDES
from mm_agent.types import Aliases, Config, Fill, Job, NormaliseError, Order, Resolved, Table


def resolve(aliases: Aliases, job: Job) -> Resolved:
    return Resolved(
        asset=_alias(aliases.assets, "asset", job.asset),
        venue=None if job.venue is None else _alias(aliases.venues, "venue", job.venue),
        pair=None if job.quote is None else _alias(aliases.pairs, "pair", f"{job.asset}/{job.quote}"),
    )


def _alias(names: dict[str, str], kind: str, name: str) -> str:
    if name not in names:
        raise NormaliseError(f"no alias for {kind} {name!r}")
    return names[name]


class Normaliser:
    def __init__(self, config: Config) -> None:
        self.orders = config.orders
        self.fills = config.fills
        self.zone = ZoneInfo(config.source.timezone)

    def order(self, row: dict[str, Any]) -> Order:
        pick = _picker(row, self.orders, "orders")
        return Order(
            id=_text(*pick("id")),
            venue=_text(*pick("venue")),
            pair=_text(*pick("pair")),
            side=_side(*pick("side")),
            price=_decimal(*pick("price")),
            qty=_decimal(*pick("qty")),
            placed=_millis(*pick("placed"), self.zone),
            ended=_optional_millis(*pick("ended"), self.zone),
            filled=_optional_decimal(*pick("filled")),
            remaining=_optional_decimal(*pick("remaining")),
        )

    def fill(self, row: dict[str, Any]) -> Fill:
        pick = _picker(row, self.fills, "fills")
        return Fill(
            venue=_text(*pick("venue")),
            pair=_text(*pick("pair")),
            order=_text(*pick("order")),
            ts=_millis(*pick("ts"), self.zone),
            price=_decimal(*pick("price")),
            qty=_decimal(*pick("qty")),
            side=_side(*pick("side")),
        )


def _picker(row: dict[str, Any], table: Table, name: str) -> Callable[[str], tuple[Any, str]]:
    def pick(field: str) -> tuple[Any, str]:
        column = table.map.get(field)
        if column is None:
            return None, f"{name}.{field}"
        if column not in row:
            raise NormaliseError(f"{name}.{field}: row has no column {column!r}")
        return row[column], f"{name}.{field} ({column})"

    return pick


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _present(value: Any, path: str) -> Any:
    if _blank(value):
        raise NormaliseError(f"{path}: is null")
    return value.strip() if isinstance(value, str) else value


def _text(value: Any, path: str) -> str:
    value = _present(value, path)
    if isinstance(value, bool | float):
        raise NormaliseError(f"{path}: expected text, got {type(value).__name__}")
    return str(value)


def _side(value: Any, path: str) -> str:
    key = str(_present(value, path)).lower()
    if key in BUY_SIDES:
        return "buy"
    if key in SELL_SIDES:
        return "sell"
    raise NormaliseError(f"{path}: unknown side {value!r}")


def _decimal(value: Any, path: str) -> str:
    value = _present(value, path)
    if isinstance(value, bool):
        raise NormaliseError(f"{path}: expected a number, got bool")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = repr(value)
    elif isinstance(value, Decimal | str):
        text = str(value)
    else:
        raise NormaliseError(f"{path}: expected a number, got {type(value).__name__}")
    if not DECIMAL_PATTERN.fullmatch(text):
        raise NormaliseError(f"{path}: {value!r} is not a decimal number")
    return text


def _optional_decimal(value: Any, path: str) -> str | None:
    return None if _blank(value) else _decimal(value, path)


def _optional_millis(value: Any, path: str, zone: ZoneInfo) -> int | None:
    return None if _blank(value) else _millis(value, path, zone)


def _millis(value: Any, path: str, zone: ZoneInfo) -> int:
    value = _present(value, path)
    if isinstance(value, bool):
        raise NormaliseError(f"{path}: expected a timestamp, got bool")
    if isinstance(value, datetime):
        return _from_datetime(value, zone)
    if isinstance(value, int | float):
        return _from_epoch(value, path)
    if isinstance(value, str):
        try:
            return _from_epoch(float(value) if "." in value or "e" in value.lower() else int(value), path)
        except ValueError:
            pass
        try:
            return _from_datetime(datetime.fromisoformat(value), zone)
        except ValueError as e:
            raise NormaliseError(f"{path}: {value!r} is not an epoch or ISO-8601 timestamp") from e
    raise NormaliseError(f"{path}: expected a timestamp, got {type(value).__name__}")


def _from_datetime(value: datetime, zone: ZoneInfo) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    return round(value.timestamp() * 1000)


def _from_epoch(value: float, path: str) -> int:
    if (isinstance(value, float) and not math.isfinite(value)) or value < 0:
        raise NormaliseError(f"{path}: {value!r} is not a valid epoch")
    if value < EPOCH_SECONDS_BELOW:
        return round(value * 1000)
    if value < EPOCH_MILLIS_BELOW:
        return round(value)
    return round(value / 1000)
