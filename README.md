# cane

A crypto trading bot that follows the **CDC Action Zone** system by Chaloke Sambhandharaksa,
trading **USDT-M perpetual futures and spot**. It reads price data from the exchange, detects
trend-change signals, weighs the supporting factors, turns that into a position size, and places
the orders itself.

The market is set **per symbol**, not for the whole system, so one profile can hold BTC on perp
and ETH on spot at the same time.

On **perp** it trades **both sides** — the first green bar opens a long; the first red bar closes
the long and opens a short within the same bar. On **spot** it is long-only: a red bar sells out to
flat and stops there, because there is no short leg to open. Leverage, funding and liquidation —
and therefore the liquidation-buffer risk check — exist on perp only.

Principles the whole system is built around:

- **Decide on closed bars only** — Action Zone colors repaint until a bar closes, so deciding on a
  live bar means acting on signals that disappear later.
- **Size comes from a formula, not a feeling** — `base_pct` is a constant, never tied to the judge's
  confidence.
- **Fail closed** — an incomplete set of risk limits means the config does not load, which means no
  trading.
- **One side per symbol, always** — one-way mode; opposing positions are never held together.

Full specs live in [docs/README.md](docs/README.md); the reasoning behind each choice is in
[docs/decisions.md](docs/decisions.md).

## Status

Specs and ADRs are complete. The code is being built out in the order laid out by the
[runtime pipeline](docs/spec/08-runtime-pipeline.md).

**There is no entry point yet** — the bot cannot be run. What exists is modules and their tests.

| Component | Status |
| --- | --- |
| `config/` — fail-closed validation, every problem reported with its field path | ✅ |
| `log.py` — strips credentials from log output on the way out | ✅ |
| `data/` — closed-bar OHLCV (live and replay share one `as_of` axis), funding rate, disk cache, ccxt client | ✅ |
| `db/` — PostgreSQL foundation: schema, migrations, repositories, append-only enforced by grants | ✅ |
| `db/repo/decisions.py` — the per-bar decision record and its six child tables | ✅ |
| Action Zone computation → `zone`, `state`, `long_signal`, `short_signal` | ⬜ |
| Confluence Judge (LLM weighing the supporting factors) | ⬜ |
| Position sizing + the discipline rules + cold start | ⬜ |
| Risk limits, kill switch, broker, reconciliation | ⬜ |
| Per-bar-close runner that fills the decision record in | ⬜ |
| Console (FastAPI + Jinja2 + HTMX) and notifications | ⬜ |

The data layer **never reads an API key**. Both OHLCV and funding rate are public endpoints, so the
rule "the paper profile never touches credentials" holds structurally rather than by the author's
care — there is nothing to leak because nothing is accepted in.

## Getting started

Requires Python 3.11+ (for stdlib `tomllib`) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev                          # install dependencies + pytest
uv run --extra dev pytest -q -m "not db"     # 70 passing, no services needed
```

`pytest` is an optional dependency — skipping `uv sync --extra dev` and running a bare
`uv run pytest` will fail.

The full suite (153 tests) needs PostgreSQL; see [Database](#database) below. Tests that touch
persistence carry the `db` marker so the rest still runs anywhere.

The test suite never touches the network: the exchange client is injected everywhere, never
constructed inside the code under test.

## Database

Everything the system remembers lives in PostgreSQL — bars, decisions, the trade ledger, and
eventually the config itself ([ADR 22](docs/decisions.md)). Access goes through SQLAlchemy Core;
there is no ORM.

```bash
docker compose up -d db                                   # postgres:16-alpine on host port 5436
cp .env.example .env                                      # CANE_DB_DSN lives here
uv run --env-file .env alembic upgrade head
uv run --env-file .env --extra dev pytest -q              # 153 passing
```

Host port **5436**, not 5432 — the dev machine already has other Postgres containers on 5432 and
5435, and a port clash looks like a broken database rather than a busy port.

Two database roles carry the append-only guarantee ([ADR 23](docs/decisions.md)): `cane_engine`
may `SELECT` and `INSERT` on fact tables and has no `UPDATE`/`DELETE` at all, while `cane_console`
reads everything and writes only config and state. `tests/test_db_grants.py` fires the forbidden
statements and asserts the database refuses them — a constraint nobody has tried to break is a
constraint nobody knows is live.

## Config

**The database is the source of truth** ([ADR 18](docs/decisions.md)). The TOML files are the way
in, once:

```bash
uv run --env-file .env cane db seed --profile paper --from config/paper.toml
uv run --env-file .env cane db seed --profile live  --from config/live.toml
```

`paper` and `live` are separate profiles running the exact same code path
([ADR 9](docs/decisions.md)) — separate sets of rows, not separate files.

| Profile | Purpose |
| --- | --- |
| [`config/paper.toml`](config/paper.toml) | Simulated broker with a `seed_quote`; sends no real orders and never reads `.env` |
| [`config/live.toml`](config/live.toml) | ccxt broker against Binance — both profiles currently set `dry_run = true` |

Every change writes a **new version**; none overwrites the last one, and every old version stays
readable. `is_active` is a pointer, not a value: the console may `UPDATE` that one column and
nothing else, so the content of a saved version cannot be rewritten even by the console. Seeding
values that match the active version creates nothing — running `cane db seed` twice is a no-op, so
setup is repeatable without filling the history with identical versions.

The point of versioning is a question the old file-based config could not answer: *which `base_pct`
decided this bar?* A decision row carries `config_version_id`, so the answer is a foreign key rather
than a guess from `git log`.

Validation is fail-closed and reports **every** problem at once, each carrying the field path the
console form uses for its inputs:

```python
from cane.config import validate_settings, ConfigError

try:
    settings = validate_settings(values_from_the_form, source="console")
except ConfigError as exc:
    for problem in exc.problems:
        print(problem.field_path, problem.message)   # symbols[0].leverage, ...
```

A value finer than the store can hold is refused rather than rounded: percentages and multipliers
keep four decimals, money keeps eight. Rounding `base_pct = 10.00001` down to `10.0000` would have
the system trade on a number nobody typed, which is the failure ADR 18 exists to prevent.

Every model is deliberately `extra="forbid"`: a mistyped key fails validation instead of vanishing
quietly and leaving the system running on a default nobody chose. No risk limit has a default value,
in the schema either — those columns are `NOT NULL`, so an incomplete set of limits cannot be stored,
which means there is no version to activate, which means no trading.

The database repeats most of these rules as `CHECK` constraints, and that duplication is deliberate:
a `CHECK` is the last word and cannot be bypassed, but it fails on the first violation it meets. The
validator exists to list all of them in one pass. One rule lives only in the validator — a symbol's
`leverage` against the profile's `max_leverage` — because it spans two tables and SQL cannot express
it as a `CHECK`.

### Credentials

Copy [`.env.example`](.env.example) to `.env` and fill in the real values. `.env` is in
`.gitignore` — **never commit it**. It also holds `CANE_DB_DSN`, which is why `alembic.ini` has no
`sqlalchemy.url` line and commands are run as `uv run --env-file .env ...`. The `paper` profile does not read this file and calls no
order-placing endpoint.

`log.py` masks the values of sensitive keys (`key`, `secret`, `token`, `password`, `passphrase`)
and `0x…` addresses before anything is written to a log.

## Layout

```
src/cane/
  config/       schema + profile loader (fail-closed, line-numbered errors)
  data/         OHLCV, funding rate, cache, ccxt client
  db/           engine (role per connection), schema, type boundary
    repo/       one module per domain; returns the project's frozen dataclasses
  log.py        credential redaction for logs
  cli.py        the `cane` command; today it only carries `cane db seed`
alembic/        migrations, one per domain; the DSN comes from the environment
docker-compose.yml  PostgreSQL for dev and tests
config/         paper / live profiles
docs/spec/      system specs — readable in order, each file self-contained
docs/decisions.md   27 ADRs with their reasoning
reference/      sources — the actual Pine Script and the trading principles it came from
tests/          no network access; `-m "not db"` needs no services either
```
