import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum


class ConfigError(ValueError):
    pass


class ControlError(RuntimeError):
    pass


class NormaliseError(ValueError):
    pass


class DriverError(RuntimeError):
    pass


class LandingError(RuntimeError):
    pass


class SourceKind(StrEnum):
    POSTGRES = "postgres"
    MYSQL = "mysql"
    CLICKHOUSE = "clickhouse"
    SQLITE = "sqlite"
    FILES = "files"


@dataclass(frozen=True)
class Order:
    id: str
    venue: str
    pair: str
    side: str
    price: str
    qty: str
    placed: int
    ended: int | None
    filled: str | None = None
    remaining: str | None = None


@dataclass(frozen=True)
class Fill:
    id: str = field(init=False)
    venue: str
    pair: str
    order: str
    ts: int
    price: str
    qty: str
    side: str

    def __post_init__(self) -> None:
        content = json.dumps(
            [self.venue, self.pair, self.order, self.ts, self.price, self.qty, self.side], separators=(",", ":")
        )
        object.__setattr__(self, "id", hashlib.sha256(content.encode()).hexdigest())


@dataclass(frozen=True)
class Job:
    start: int
    end: int
    asset: str
    venue: str | None = None
    quote: str | None = None

    @property
    def label(self) -> str:
        return f"{self.asset} {self.venue or 'all'}/{self.quote or 'all'}"


@dataclass(frozen=True)
class Resolved:
    asset: str
    venue: str | None = None
    pair: str | None = None


@dataclass(frozen=True)
class Control:
    url: str


@dataclass(frozen=True)
class Source:
    kind: SourceKind
    dsn: str
    timezone: str


@dataclass(frozen=True)
class Aliases:
    assets: dict[str, str]
    venues: dict[str, str]
    pairs: dict[str, str]


@dataclass(frozen=True)
class Table:
    query: str
    map: dict[str, str]


@dataclass(frozen=True)
class Config:
    mm: str
    control: Control
    source: Source
    aliases: Aliases
    orders: Table
    fills: Table
