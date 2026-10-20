import re
from datetime import UTC, datetime
from pathlib import Path

from mm_agent.types import ControlError, DriverError, LandingError, NormaliseError, SourceKind

DEFAULT_TIMEZONE = "UTC"
DEFAULT_CONFIG_PATH = Path("/agent.yaml")
TOKEN_ENV = "FORGD_AGENT_TOKEN"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
IMAGE_DIGEST_ENV = "FORGD_IMAGE_DIGEST"
TICK_SECONDS = 300
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
SAMPLE_ROWS = 5
HOUR_MILLIS = 3_600_000
LABEL_DRY_RUN = "dry run"
LABEL_CONFORM = "conformance gate"
TIMEOUT_SECONDS = 30
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
EPOCH_SECONDS_BELOW = 10**11
EPOCH_MILLIS_BELOW = 10**14
BUY_SIDES = frozenset({"buy", "b", "bid", "1"})
OPTIONAL_ORDER_FIELDS = frozenset({"filled", "remaining"})
DERIVED_FILL_FIELDS = frozenset({"id"})
SELL_SIDES = frozenset({"sell", "s", "ask", "-1"})
DECIMAL_PATTERN = re.compile(r"^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?$")
FETCH_ROWS = 1000
DSN_DIALECTS = {
    SourceKind.POSTGRES: frozenset({"postgresql"}),
    SourceKind.MYSQL: frozenset({"mysql"}),
    SourceKind.CLICKHOUSE: frozenset({"clickhousedb"}),
    SourceKind.SQLITE: frozenset({"sqlite"}),
}
MYSQL_SCHEME = "mysql+pymysql"
DATE_FORMAT = "%Y-%m-%d"
FILE_ENCODING = "utf-8-sig"
GZIP_SUFFIX = ".gz"
CSV_SUFFIX = ".csv"
JSONL_SUFFIX = ".jsonl"
LANDING_NAMES = frozenset({"orders.raw", "orders.mapped", "fills.raw", "fills.mapped"})
LANDING_SUFFIX = ".jsonl.gz"
MAPPED_SUFFIX = ".mapped"
LANDING_CONTENT_TYPE = "application/gzip"
GZIP_MTIME = 0
RECEIPT_KIND_JOB = "job"
RECEIPT_KIND_RUN = "run"
STATUS_OK = "ok"
STATUS_FAILED = "failed"
VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
CHECK_LANDS = "job lands"
CHECK_COLUMNS = "query returns every mapped column"
CHECK_FIELDS = "map covers every schema field"
CHECK_WINDOW = "timestamps fall inside the window"
CHECK_ENDED = "open orders have null ended"
CHECK_REFERENCES = "every fill references an order in the pull"
CHECK_STABLE = "two consecutive runs hash identical"
CHECK_COUNTS = "orders and fills are non-empty"
JOB_ERRORS = (ControlError, DriverError, LandingError, NormaliseError)
