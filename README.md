# forgd-mm-agent

The container a market maker runs so that Forgd can verify their activity from raw data instead of self-reported numbers.

It copies two tables out of your order management system, orders and fills, for the time windows and assets Forgd asks for, and lands them in Forgd's bucket. Nothing is computed on your side. What you configure is one read-only connection, a map from Forgd's asset names to yours, and a map from Forgd's column names to yours.

## What it does

Every X minutes the agent:

1. Checks in with Forgd's control plane: agent version, a hash of your config, the image digest if you passed one, and the time.
2. Asks which jobs are outstanding. A job is one asset and one half-open time window `[from, to)` in UTC. Usually it asks for everything you hold for that asset, across every venue and pair. Forgd can narrow a job to one venue, one quote currency, or both.
3. For each job, resolves your aliases, runs your configured query or file glob with the window and asset bound, drops rows outside a narrowed job's venue or pair, and streams the rows into two gzipped JSONL files: the raw rows exactly as your source returned them, and the same rows mapped to Forgd's schema.
4. Asks the control plane for a signed upload URL per file and PUTs the file there. One URL, one object, short lifetime, path chosen by Forgd.
5. Sends a receipt per job and one per pass: row counts, byte sizes, content hashes, query time, and any error.

Then it sleeps and goes again. The control plane decides the cadence, the lag, backfills and re-pulls. A failed job stays outstanding and is retried on the next pass. The agent keeps no state between passes, so you can restart it at any time.

## What it does not do

- It opens no inbound port. All traffic is outbound: to the control plane URL in your config and to the signed upload URLs it hands back.
- It holds no cloud credentials. The only secret in the container is `FORGD_AGENT_TOKEN`, which identifies you to the control plane. Revoking it cuts the agent off.
- It never sends your database credentials, your query text, or your config anywhere. The control plane sees a hash of the config, not the config.
- It never writes to your source. Use a read-only database user or a read-only mount.
- It never updates itself. The image you pulled is the image that runs, until you pull a new tag.
- It computes nothing. No metrics, no spreads, no uptime. Those are derived on Forgd's side from the raw rows.

`docker stop` is the off switch.

## Quick start

1. Copy the example closest to your setup from `examples/` to `agent.yaml` and edit it.
2. Dry run it against a window you know. No token is needed and nothing leaves the machine:

   ```sh
   docker run --rm -v ./agent.yaml:/agent.yaml:ro ghcr.io/forged-com/mm-agent:0.1.0 --dry-run --day 2026-10-17
   ```

   The report prints the resolved aliases, the exact query with every bound value, counts and hashes per file, the first rows of each file, and the receipts it would have sent.
3. Run it for real with the token Forgd issued you:

   ```sh
   docker run -d --restart unless-stopped \
     -e FORGD_AGENT_TOKEN=... \
     -v ./agent.yaml:/agent.yaml:ro \
     ghcr.io/forged-com/mm-agent:0.1.0
   ```

For a `files` source add `-v ./exports:/exports:ro`. For a `sqlite` source mount the database file read-only at the path your DSN names.

Each release publishes its image digest in `RELEASES.md`. Pass it through so Forgd can see exactly which build is running:

```sh
-e FORGD_IMAGE_DIGEST=sha256:...
```

Without Docker, install from a release checkout with `pip install ".[postgres]"` (or `mysql`, `clickhouse`) and run `forgd-mm-agent agent.yaml`.

## Configuration

One file, `agent.yaml`. Unknown keys are rejected, so a typo fails at startup rather than silently.

```yaml
mm: alpha
control:
  url: https://mmcp.forgd.com
source:
  kind: postgres
  dsn: postgresql://readonly@oms-db/oms
  timezone: UTC
aliases:
  assets: {ETH: ETH, BTC: XBT}
  venues: {binance: BINANCE_SPOT, okx: OKX}
  pairs: {ETH/USDT: ETHUSDT, BTC/USDT: BTCUSDT}
orders:
  query: |
    select order_id, venue, symbol, side, price, orig_qty, created_at, closed_at, filled_qty, orig_qty - filled_qty as remaining_qty
    from orders
    where base_asset = :asset
      and created_at < :to
      and (closed_at is null or closed_at >= :from)
  map: {id: order_id, venue: venue, pair: symbol, side: side, price: price, qty: orig_qty, placed: created_at, ended: closed_at, filled: filled_qty, remaining: remaining_qty}
fills:
  query: |
    select order_id, venue, symbol, exec_time, px, qty, side
    from fills
    where base_asset = :asset
      and exec_time >= :from and exec_time < :to
  map: {venue: venue, pair: symbol, order: order_id, ts: exec_time, price: px, qty: qty, side: side}
```

| Key | Meaning |
| --- | --- |
| `mm` | Your identifier as agreed with Forgd. |
| `control.url` | The control plane base URL. Must be `https://`; plain `http://` is accepted only for loopback hosts. |
| `source.kind` | `postgres`, `mysql`, `clickhouse`, `sqlite` or `files`. |
| `source.dsn` | A SQLAlchemy URL for the SQL kinds, or a directory path for `files`. The scheme must match the kind, see below. |
| `source.timezone` | IANA zone of naive timestamps in your source. Defaults to `UTC`. Set it if your database stores local time. |
| `aliases.assets` | Required. Forgd's asset name on the left, the string your query matches on the right. |
| `aliases.venues` | Forgd's venue name on the left, the string in your rows on the right. Needed only if Forgd narrows a job to a venue. |
| `aliases.pairs` | Forgd's pair name, `ASSET/QUOTE`, on the left, the string in your rows on the right. Needed only if Forgd narrows a job to a quote. |
| `orders.query`, `fills.query` | A SQL statement for the SQL kinds, or a glob for `files`. |
| `orders.map`, `fills.map` | Forgd's field name on the left, your column on the right. Every schema field must be mapped and nothing else may be, except `filled` and `remaining` on orders, which may be left out. `venue` and `pair` must be columns on every row, because one job can span several of each. |

A job whose asset, venue or pair has no alias fails with that reason in the receipt, and the other jobs carry on. The agent never guesses an asset.

### Source kinds and DSN schemes

| Kind | DSN scheme | Driver |
| --- | --- | --- |
| `postgres` | `postgresql://` | psycopg 3, included in the image |
| `mysql` | `mysql+pymysql://` | PyMySQL, included in the image. A bare `mysql://` is rejected at startup because it would select a driver the image does not carry. |
| `clickhouse` | `clickhousedb://` | clickhouse-connect, included in the image. `clickhouse://` and `clickhouse+native://` are rejected. |
| `sqlite` | `sqlite:///` | Python's built-in sqlite3. Four slashes for an absolute path. |
| `files` | a directory | No driver. CSV or JSONL files, gzipped or not. |

### Bound parameters for SQL sources

Your query is run as written. The agent binds five named parameters and does nothing else to it, so the window and asset logic is yours. The only filter applied afterwards is a narrowed job's venue and pair.

| Parameter | Value |
| --- | --- |
| `:from`, `:to` | The window edges as naive timestamps in `source.timezone`. Compare these against columns that store naive local or UTC time. |
| `:from_utc`, `:to_utc` | The same edges as timezone-aware UTC timestamps. Compare these against aware columns, or convert them to epoch for sources that store epoch numbers. |
| `:asset` | Your string for the job's asset, after alias resolution. Return every venue and pair you hold for it. |

The window semantics Forgd expects:

- Orders: anything open at any point in the window. `placed < :to and (ended is null or ended >= :from)`.
- Fills: `:from <= ts < :to`.

The same order can legitimately appear in several windows. Forgd deduplicates downstream; the agent does not.

### Files sources

`source.dsn` is the root directory and each `query` is a glob relative to it. The placeholder `{asset}` expands to your alias string and `{from}` and `{to}` to dates formatted `YYYY-MM-DD` in `source.timezone`. Files ending in `.csv` or `.jsonl`, optionally `.gz`, are read in path order. CSV needs a header row and every value arrives as a string. A UTF-8 byte order mark is tolerated.

Unlike SQL, the files driver also filters by window: every row from every matched file is normalised and then kept only if it falls inside the window by the rules above and inside a narrowed job's venue and pair. Other rows are dropped and not counted.

## Schema

**orders**: `id`, `venue`, `pair`, `side`, `price`, `qty`, `placed`, `ended`, `filled`, `remaining`

**fills**: `id`, `venue`, `pair`, `order`, `ts`, `price`, `qty`, `side`

`venue` and `pair` are written in your own strings, exactly as your source returns them.

`ended` is null while the order is still open. `qty` is the quantity the order was placed with; `filled` is the cumulative quantity executed and `remaining` the quantity still resting, both at the time your source reports them. Map them if your source has them, since an order that was partially filled before a window opened cannot be sized correctly without them. If you cannot, leave them out of the map and they are written as null. Everything else is required.

`id` on fills is not mapped. The agent derives it as the SHA-256 of the mapped `venue`, `pair`, `order`, `ts`, `price`, `qty` and `side`, so the same fill pulled in two overlapping windows carries the same id and Forgd can dedupe by it. The mapped file is what Forgd reads; the raw file is kept alongside so a mapping mistake can be fixed without asking you to re-run.

How values are coerced:

- Timestamps become UTC epoch milliseconds. Accepted inputs: epoch numbers or numeric strings, with the unit inferred from magnitude (below 1e11 is seconds, below 1e14 is milliseconds, otherwise microseconds); ISO 8601 strings; datetime values from the driver. Naive values are read in `source.timezone`; aware values keep their offset.
- Side is case-insensitive. `buy`, `b`, `bid` and `1` become `buy`; `sell`, `s`, `ask` and `-1` become `sell`. Anything else is an error.
- Price and quantity are kept as decimal strings, never rounded or reformatted. Strings must already parse as a decimal number. Floats are written with their shortest exact representation.
- Ids are written as strings. Floats and booleans are rejected as ids.
- Venues and pairs are written as strings, trimmed of surrounding whitespace. Floats and booleans are rejected.
- Null, meaning a missing value or a blank string, is allowed only in `ended`, `filled` and `remaining`.

Any coercion failure fails the whole job, naming the table, field and column, and no partial file is uploaded.

### Timestamp warnings

- **Compact dates look like epochs.** A string such as `20261017` is read as epoch seconds, not as 17 October 2026, because the two cannot be told apart. Use `2026-10-17` or a full ISO timestamp.
- **Local time around DST changes.** If your source stores naive local time, the repeated hour at the autumn change resolves to its first occurrence, and times inside the spring gap resolve with the offset in force before the change. Store UTC if you can. If you cannot, expect this and check the dry run around a change.
- **File globs cover one local date.** A UTC day window in a non-UTC `source.timezone` spans two local dates, but a glob using `{from}` matches only one. Rows from the other date are silently dropped by the window filter. The dry run lists every matched path, so check it before going live, and prefer file layouts that do not partition by date, or set `source.timezone` to `UTC` for files that are already in UTC.

## Dry run

```sh
forgd-mm-agent agent.yaml --dry-run --from 2026-10-17T12:00Z --to 2026-10-17T13:00Z
forgd-mm-agent agent.yaml --dry-run --day 2026-10-17
```

A dry run performs every step except the two that leave the machine: no check-in, no upload. It needs no token. It runs one full-dump job per configured asset, prints the full report to stdout, and exits 0 if every job succeeded and 1 otherwise. Instants without an offset are read as UTC. `--day` is shorthand for that UTC day.

This is also the onboarding session: Forgd will ask you to run it against a known window and share the output before any token is issued.

## Conformance gate

```sh
forgd-mm-agent agent.yaml --conform --from 2026-10-17T00:00Z --to 2026-10-18T00:00Z
forgd-mm-agent agent.yaml --conform --day 2026-10-17
```

The gate is the dry run plus assertions, and it is what Forgd asks for before onboarding is agreed. Pick a window of at least an hour in which you know each configured asset traded. The gate runs the dry run on that window and again on its first hour, so a query that only works for days or only for hours is caught, and lands every job twice. After each job it prints one `PASS` or `FAIL` line per check:

- the query returned every column the map names, on every row;
- the map covers every schema field, noting any optional ones left unmapped;
- every `placed`, `ended` and `ts` falls inside the window the agent asked for, by the same rule the agent uses: an order counts if `placed < to` and `ended` is null or `>= from`, a fill if `from <= ts < to`;
- `ended` is null for open orders, never a sentinel: when set it lies between `placed` and now;
- every fill references an order on the same venue and pair in the same pull;
- two consecutive runs produce identical row counts and hashes for all four files;
- both tables are non-empty for every job.

A job that fails to land prints that error as its only check. The report ends with one `conformance PASS` or `conformance FAIL` line and the exit code is 0 only on a pass. Share the whole output with Forgd. Nothing leaves the machine and no token is needed.

## Receipts

Every job receipt carries the asset, venue and quote as Forgd named them and as you alias them, the window, the status, per landed file the row count, size and SHA-256 of the uncompressed JSONL, the query time in milliseconds, and the error text if it failed. Every pass ends with a run receipt carrying the agent version, config hash, image digest, start and end time, jobs attempted, succeeded and failed, and the errors. Forgd mirrors these to your dashboard, so what you see is what the agent sent.

A check-in is sent on every pass even when there is nothing to do. That is how Forgd tells a stopped agent from a quiet one.

## Wire contract

The four endpoints, the job shape, the receipt shape and the two row schemas are pinned as JSON Schema under `contract/v1/`. The agent's own tests validate the real payloads against them.

`contract/v1/` is frozen from the first tagged release, so an agent on v1 can stay on v1:

- The schemas describe what v1 sends. A receiver ignores fields it does not know, so either side can add a field without breaking the other.
- The fill `id` hash does not change within v1, because the same fill must keep the same id across releases.
- A breaking change goes in `contract/v2/`, and Forgd keeps serving v1 alongside it.

## Releases

Each release is a tagged version with notes in `RELEASES.md`: what changed, a worked example, what you need to do, and the image digest. The agent carries no business logic, so changes on Forgd's side never need a release from you. Expect one only for an agent bug, a new driver, or a security fix, and you only need to update if the notes say it affects you.

## Development

```sh
make install
make check
```

`make install` creates a conda environment with the package, its dev extras and the three database drivers. `make check` runs ruff, mypy in strict mode and the test suite; the postgres, MySQL and ClickHouse conformance cases skip unless their DSN environment variables are set. `make conform` starts those three servers with `docker compose` from `tests/compose.yaml` and runs the conformance suite against them. The Hummingbot example is tested as well: the suite builds a database in Hummingbot's own schema from the golden fixture and lands the example YAML against it. `make ci` is what GitHub Actions runs on every push and pull request (`.github/workflows/ci.yml`): it builds the `test` stage of the Dockerfile, runs the full check inside it against the three compose servers, then builds the shipping image and runs `--help` in it. The shipping image never contains the tests or the dev tools.
