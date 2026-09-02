# cane

A crypto trading bot that follows the **CDC Action Zone** system by Chaloke Sambhandharaksa,
trading **USDT-M perpetual futures**. It reads price data from the exchange, detects trend-change
signals, weighs the supporting factors, turns that into a position size, and places the orders itself.

It trades **both sides** — the first green bar opens a long; the first red bar closes the long and
opens a short within the same bar.

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
| `config/` — fail-closed TOML loader, every problem reported with its line number | ✅ |
| `log.py` — strips credentials from log output on the way out | ✅ |
| `data/` — closed-bar OHLCV (live and replay share one `as_of` axis), funding rate, disk cache, ccxt client | ✅ |
| `db/` — PostgreSQL foundation: schema, migrations, repositories, append-only enforced by grants | ✅ |
| Action Zone computation → `zone`, `state`, `long_signal`, `short_signal` | ⬜ |
| Confluence Judge (LLM weighing the supporting factors) | ⬜ |
| Position sizing + the discipline rules + cold start | ⬜ |
| Risk limits, kill switch, broker, reconciliation | ⬜ |
| Per-bar-close runner + DecisionRecord | ⬜ |
| Console (FastAPI + Jinja2 + HTMX) and notifications | ⬜ |

The data layer **never reads an API key**. Both OHLCV and funding rate are public endpoints, so the
rule "the paper profile never touches credentials" holds structurally rather than by the author's
care — there is nothing to leak because nothing is accepted in.

## Getting started

Requires Python 3.11+ (for stdlib `tomllib`) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev                          # install dependencies + pytest
uv run --extra dev pytest -q -m "not db"     # 57 passing, no services needed
```

`pytest` is an optional dependency — skipping `uv sync --extra dev` and running a bare
`uv run pytest` will fail.

The full suite (96 tests) needs PostgreSQL; see [Database](#database) below. Tests that touch
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
uv run --env-file .env --extra dev pytest -q              # 96 passing
```

Host port **5436**, not 5432 — the dev machine already has other Postgres containers on 5432 and
5435, and a port clash looks like a broken database rather than a busy port.

Two database roles carry the append-only guarantee ([ADR 23](docs/decisions.md)): `cane_engine`
may `SELECT` and `INSERT` on fact tables and has no `UPDATE`/`DELETE` at all, while `cane_console`
reads everything and writes only config and state. `tests/test_db_grants.py` fires the forbidden
statements and asserts the database refuses them — a constraint nobody has tried to break is a
constraint nobody knows is live.

## Config

`paper` and `live` are separate files that run the exact same code path
([ADR 9](docs/decisions.md)).

| File | Purpose |
| --- | --- |
| [`config/paper.toml`](config/paper.toml) | Simulated broker with a `seed_quote`; sends no real orders and never reads `.env` |
| [`config/live.toml`](config/live.toml) | ccxt broker against Binance — both files currently set `dry_run = true` |

Every model is deliberately `extra="forbid"`: a mistyped key fails the load instead of vanishing
quietly and leaving the system running on a default nobody chose. No risk limit has a default value.

```python
from cane.config import load_profile, ConfigError

try:
    settings = load_profile("config/paper.toml")
except ConfigError as exc:
    for problem in exc.problems:
        print(problem)   # each carries the line number
```

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
alembic/        migrations, one per domain; the DSN comes from the environment
docker-compose.yml  PostgreSQL for dev and tests
config/         paper / live profiles
docs/spec/      system specs — readable in order, each file self-contained
docs/decisions.md   25 ADRs with their reasoning
reference/      sources — the actual Pine Script and the trading principles it came from
tests/          no network access; `-m "not db"` needs no services either
```
