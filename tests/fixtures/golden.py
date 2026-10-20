import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from mm_agent.landing import Writer
from mm_agent.types import Fill, Job, Order, SourceKind
from mm_agent.window import holds_fill, holds_order

ROOT = Path(__file__).parent / "golden"
MM = "golden"
CONTROL_URL = "https://mmcp.invalid"
START = 1_792_879_200_000
END = 1_792_969_200_000
FOLD_START = 1_792_890_000_000
FOLD_END = 1_792_893_600_000
UTC_MIDNIGHT = 1_792_886_400_000
LOCAL_TIMEZONE = "Europe/Berlin"
VENUES = {"binance": "BINANCE_SPOT"}
PAIRS = {"BTC/USDT": "BTCUSDT", "ETH/USDT": "ETHUSDT"}
MAPPED = ("orders.mapped", "fills.mapped")
ORDER_MAP = {
    "id": "order_id",
    "side": "side",
    "price": "price",
    "qty": "orig_qty",
    "placed": "created_at",
    "ended": "closed_at",
    "filled": "filled_qty",
    "remaining": "remaining_qty",
}
FILL_MAP = {"order": "order_id", "ts": "exec_time", "price": "px", "qty": "qty", "side": "side"}
ORDER_FIELDS = ("id", "side", "price", "qty", "placed", "ended", "filled", "remaining")
FILL_FIELDS = ("order", "ts", "price", "qty", "side")
TIMEZONES = dict.fromkeys(SourceKind, "UTC") | {SourceKind.MYSQL: LOCAL_TIMEZONE}
EDGES = dict.fromkeys(SourceKind, ("from_utc", "to_utc")) | {
    SourceKind.MYSQL: ("from", "to"),
    SourceKind.SQLITE: ("from", "to"),
}
ORDERS_SQL = """
select order_id, side, price, orig_qty, created_at, closed_at, filled_qty, remaining_qty
from orders
where venue = :venue and symbol = :pair
  and created_at < :{to}
  and (closed_at is null or closed_at >= :{from})
order by created_at, order_id
"""
FILLS_SQL = """
select order_id, exec_time, px, qty, side
from fills
where venue = :venue and symbol = :pair
  and exec_time >= :{from} and exec_time < :{to}
order by exec_time, order_id
"""
ORDERS_GLOB = "orders/{venue}/{pair}.csv"
FILLS_GLOB = "fills/{venue}/{pair}.csv"


def orders() -> list[dict[str, Any]]:
    return _rows("orders")


def fills() -> list[dict[str, Any]]:
    return _rows("fills")


def expected() -> dict[str, Any]:
    with (ROOT / "expected.json").open() as handle:
        return json.load(handle)


def jobs() -> list[Job]:
    return [Job(START, END, pair, venue) for pair in sorted(PAIRS) for venue in sorted(VENUES)]


def key(job: Job) -> str:
    return f"{job.pair}@{job.venue}"


def compute() -> dict[str, Any]:
    files = {}
    for job in jobs():
        kept_orders = [o for o in _typed_orders(job) if holds_order(job, o)]
        kept_fills = [f for f in _typed_fills(job) if holds_fill(job, f)]
        with TemporaryDirectory() as directory:
            files[key(job)] = {
                "orders.mapped": _landed(Path(directory), "orders.mapped", kept_orders),
                "fills.mapped": _landed(Path(directory), "fills.mapped", kept_fills),
            }
    return {"window": {"from": START, "to": END}, "files": files}


def yaml(kind: SourceKind, dsn: str) -> str:
    edges = dict(zip(("from", "to"), EDGES[kind], strict=True))
    files = kind is SourceKind.FILES
    return "\n".join(
        [
            f"mm: {MM}",
            "control:",
            f"  url: {CONTROL_URL}",
            "source:",
            f"  kind: {kind.value}",
            f"  dsn: {dsn}",
            f"  timezone: {TIMEZONES[kind]}",
            "aliases:",
            f"  venues: {json.dumps(VENUES)}",
            f"  pairs: {json.dumps(PAIRS)}",
            "orders:",
            f"  query: {json.dumps(ORDERS_GLOB if files else ORDERS_SQL.format_map(edges))}",
            f"  map: {json.dumps(ORDER_MAP)}",
            "fills:",
            f"  query: {json.dumps(FILLS_GLOB if files else FILLS_SQL.format_map(edges))}",
            f"  map: {json.dumps(FILL_MAP)}",
            "",
        ]
    )


def _rows(name: str) -> list[dict[str, Any]]:
    with (ROOT / f"{name}.jsonl").open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _typed_orders(job: Job) -> list[Order]:
    return [Order(**{f: row[f] for f in ORDER_FIELDS}) for row in orders() if _belongs(row, job)]


def _typed_fills(job: Job) -> list[Fill]:
    return [Fill(**{f: row[f] for f in FILL_FIELDS}) for row in fills() if _belongs(row, job)]


def _belongs(row: dict[str, Any], job: Job) -> bool:
    return bool(row["pair"] == job.pair and row["venue"] == job.venue)


def _landed(directory: Path, name: str, records: list[Any]) -> dict[str, Any]:
    with Writer(directory, name) as writer:
        for record in records:
            writer.write(record)
    landed = writer.close()
    return {"rows": landed.rows, "sha256": landed.sha256}
