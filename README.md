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
[docs/adr/](docs/adr/), indexed by [docs/decisions.md](docs/decisions.md).

## Status

Specs and ADRs are complete. The code is being built out in the order laid out by the
[runtime pipeline](docs/spec/08-runtime-pipeline.md).

The bot runs end to end now: `cane engine run --profile {live|paper}` is the per-profile loop,
normally started by the console's supervisor rather than by hand. Every closed bar goes through
the fourteen steps of the runtime pipeline — Action Zone, the Judge, sizing, the risk gates, the
order — and lands as one decision row. `paper` runs that path against a simulated venue; `live`
sends the orders through ccxt, and both profiles still ship with `dry_run = true`. Replay runs
the same pipeline over past bars in a scratch database ([ADR 29](docs/decisions.md)).

`cane serve` brings the console up. The shell is in place — layout, the profile/engine cards,
and the live↔paper mode switch — and it now sits behind two-step authentication: password,
then a TOTP code, with a fresh code required again for anything that controls the engine or
switches to `live`. Every screen except users has its body — overview, symbols, risk, log,
report, config, and the per-pair page (`/symbols/{pair}`: chart, the latest decision, and a
cold start preview where you can pick the route for the next run — [ADR 35](docs/decisions.md)).

First run, after `alembic upgrade head`:

```bash
uv run --env-file .env cane auth seed-permissions
uv run --env-file .env cane auth create-owner --email you@example.com --name "Your Name"
```

The second command prints a one-time link. Open it to set up an authenticator app; the
account stays unusable until you do — there is no path into the console that skips 2FA.

| Component | Status |
| --- | --- |
| `config/` — fail-closed validation, every problem reported with its field path | ✅ |
| `log.py` — strips credentials from log output on the way out | ✅ |
| `data/` — closed-bar OHLCV (live and replay share one `as_of` axis), funding rate, ccxt client per market; writes through `db/repo` | ✅ |
| `db/` — PostgreSQL foundation: schema, migrations, repositories, append-only enforced by grants | ✅ |
| `db/repo/decisions.py` — the per-bar decision record and its six child tables | ✅ |
| Action Zone computation → `zone`, `state`, `long_signal`, `short_signal` — verified bar-by-bar against a TradingView export | ✅ |
| `engine/` — per-profile subprocess, heartbeat, and the console-side supervisor (start/stop/status) | ✅ |
| Confluence Judge — the LLM weighing the supporting factors, its verdict cache, and the two adapters | ✅ |
| Position sizing + the discipline rules + cold start + late entry | ✅ |
| Risk limits, kill switch, broker (paper and ccxt), reconciliation | ✅ |
| Per-bar-close runner that fills the decision record in — one pipeline, shared by live and replay | ✅ |
| Console shell — layout, sidebar, profile/engine cards, mode switch, `cane serve` | ✅ |
| `auth/` — two-step login, TOTP, backup codes, account lockout, sessions, RBAC, audit log | ✅ |
| Console screens — overview, risk, log, config, symbols, report, per-pair page | ✅ |
| Console screens — users | ⬜ |
| Notifications — LINE and Telegram, the event emitter and the per-mode switches | ⬜ |

The Action Zone acceptance gate is closed: `tests/fixtures/action_zone/` holds the TradingView
export and `tests/test_action_zone.py` matches it bar by bar — both EMAs, all six zones, and the
two signals — with `tests/test_trailing.py` doing the same for the trailing stop.

The data layer **never reads an API key**. Both OHLCV and funding rate are public endpoints, so the
rule "the paper profile never touches credentials" holds structurally rather than by the author's
care — there is nothing to leak because nothing is accepted in.

## Getting started

Requires Python 3.11+ (for stdlib `tomllib`) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev                          # install dependencies + pytest
uv run --extra dev pytest -q -m "not db"     # 568 passing, no services needed
```

`pytest` is an optional dependency — skipping `uv sync --extra dev` and running a bare
`uv run pytest` will fail.

The full suite (1101 tests) needs PostgreSQL; see [Database](#database) below. Tests that touch
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
uv run --env-file .env --extra dev pytest -q              # 1101 tests
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
  data/         OHLCV, funding rate, ccxt client per market (writes to `bars` / `funding_observations`)
  db/           engine (role per connection), schema, type boundary
    repo/       one module per domain; returns the project's frozen dataclasses
  indicators/   Action Zone, the trailing stop, and the features fed to the Judge
  confluence/   the Judge — contract, prompts, verdict cache, one client per gateway
  sizing/       the size matrix: signal strength and confluence → percent of the bucket
  rules/        the discipline rules, the flip protocol, and late entry
  risk/         the three risk gates and the kill switch
  execution/    the broker contract, the paper venue, the ccxt broker, reconciliation
  engine/       per-profile subprocess, heartbeat, the console-side supervisor,
                and the per-bar pipeline that live and replay share
  api/          the console's FastAPI app, routes, and the auth dependencies
  auth/         password and TOTP primitives, and the login/step-up decisions
  web/          Jinja2 templates and static assets for the console
  log.py        credential redaction for logs
  cli.py        the `cane` command: `db seed`, `engine run`, `serve`, `auth …`
alembic/        migrations, one per domain; the DSN comes from the environment
docker-compose.yml  PostgreSQL for dev and tests
config/         paper / live profiles
docs/spec/      system specs — readable in order, each file self-contained
docs/decisions.md   ADR index — number, title, status
docs/adr/       one file per ADR, superseded ones kept and marked
reference/      sources — the actual Pine Script and the trading principles it came from
tests/          no network access; `-m "not db"` needs no services either
```
