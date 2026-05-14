# Changelog

All notable changes to **mt5-bridge** (*pykka + msgspec event bridge for MetaTrader 5*) will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

_No unreleased changes yet. New entries should be filed under one of:_
_`Added` / `Changed` / `Deprecated` / `Removed` / `Fixed` / `Security`._

---

## [0.2.0] - 2026-05-14

Engineering polish + workflow upgrades. Pure additions on top of v0.1.0
(no breaking changes to public API).

### Added

- **`mt5-bridge` CLI** (`src/mt5_bridge/__main__.py`, `[project.scripts]` entry).
  `mt5-bridge --version` prints the package version. `mt5-bridge run --config
  config.toml` starts the bridge with a default printer subscribed to every
  event type, equivalent to (but more concise than) `examples/run_bridge.py`.
- **`BarClosed` OutputEvent** + **`EventType.BAR_CLOSED = 31`**. Dispatcher
  now emits a typed event whenever a `Bar(is_closed=True)` or `HistoryBar`
  arrives, with `is_history` flag distinguishing live vs back-fill. Raw
  `Bar` / `HistoryBar` subscriptions still work (additive).
- **GitHub Actions CI** (`.github/workflows/ci.yml`). Runs ruff + mypy +
  pytest on every push / PR to master, on `windows-latest` (the only
  platform `MetaTrader5` ships wheels for).
- **ruff + mypy configuration** in `pyproject.toml` (`[tool.ruff]`,
  `[tool.ruff.lint]`, `[tool.mypy]`). Ruff selection: `E`, `W`, `F`, `I`
  (errors, warnings, pyflakes, isort). Line length 120 with per-file
  exemptions for `executor.py` and `trade_commands.py` (intentional
  aligned tables / long error messages).
- **Multi-symbol regression suite** (`tests/test_multi_symbol.py`):
  per-ticket state isolation, close-one-symbol-affects-only-one,
  per-symbol watchdog stale, executor stale set per symbol.
- **6 new BarClosed tests** in `tests/test_dispatcher_state_machine.py`.

### Changed

- `[project.optional-dependencies] dev` now pulls `ruff>=0.13`,
  `mypy>=1.18`, `pytest-cov>=6.0` in addition to `pytest`.
- `pyproject.toml` console script entry registers `mt5-bridge` on PATH
  after editable install.

### Fixed

- 14 ruff warnings cleared (9 × F541 f-string-no-placeholder, 3 × E402
  module-import-not-at-top, 2 × F401 unused-import).
- 3 mypy errors cleared:
  - `infra/scheduler.py` `ReactorScheduler._reactor` typed as `Any`
    (Twisted is lazy-imported, no static type available without forcing
    the import).
  - `actors/dispatcher.py` `_broadcast` `key` annotated as `EventType | int`
    so the `EventType(...)` → fallback `int` branch type-checks.

### Quality

- **93 / 93 tests passing** (was 82 in v0.1.0; +6 BarClosed +5 multi-symbol).
- **`ruff check src/ tests/ examples/`** clean.
- **`mypy src/mt5_bridge/`** clean (26 source files, 0 errors).

---

## [0.1.0] - 2026-05-14

Initial public release. End-to-end event-driven bridge for MetaTrader 5
covering market data ingress (Twisted+msgspec TCP), trade command execution
(pykka actors), and state-machine-based output events.

### Added

#### Public API surface

- `MT5Bridge` facade with `subscribe` / `subscribe_all` / `feed_input` /
  `kelly` / 6 fire-and-forget trade methods / `start_io` / `serve_forever` /
  `stop`.
- 6 trade commands (validated, MT5-request convertible): `OpenPosition`,
  `ClosePosition`, `ModifyPosition`, `OpenOrder`, `ModifyOrder`, `CancelOrder`.
- 7 OutputEvent types: `PositionOpened`, `PositionClosed`, `PositionModified`,
  `OrderPlaced`, `OrderModified`, `OrderCanceled`, `EmergencyTickStale`.
- EA wire-format types mirroring the V3.0 `ea_messages` schema 1:1
  (`Tick`, `HistoryTick`, `Bar`, `HistoryBar`, `Account`, `Position`, `Order`,
  `Connected`, plus tail markers).

#### Actors (pykka.ThreadingActor)

- `Dispatcher` — per-ticket state machine (open/modify/close), pub/sub fanout
  with class-key, EventType-key, and catch-all subscriptions.
- `Executor` — serial command execution with retry + exponential backoff +
  jitter (configurable retcode whitelist) + symbol-stale rejection.
- `EAReceiver` — owns the Twisted RPC subsystem lifecycle (lazy import,
  enabled only when `[rpc] enabled = true`).
- `TickWatchdog` — per-symbol last-tick tracking; emits `EmergencyTickStale`
  on broadcast and `SymbolStale` / `SymbolAlive` to the executor.
- `KellyActor` — caches latest Account; exposes `suggest_volume(...)` with
  Kelly criterion + half-Kelly default + lot-step floor + min/max clamp.

#### Infrastructure

- `infra/rpc/` — Twisted `LineReceiver` + `EAFactory` + `install_ea_listeners`,
  newline-delimited msgspec.json frames, per-endpoint binding.
- `infra/scheduler.py` — `ReactorScheduler` (rpc on) / `TimerScheduler` (rpc off)
  abstraction for non-blocking retry scheduling.
- `infra/mt5_client.py` — connection lifecycle, `assert_trade_ready()`
  pre-flight (AutoTrading / account / EA permissions), 6 trade methods with
  unified `TradeResult` shape, `symbol_info` for Kelly.
- `infra/logging.py` — production-grade structlog with async queue, rotating
  file output, context vars (pre-existing scaffold; integrated package-wide).

#### Configuration (TOML)

- `[mt5]` — account / password / server / install path (auto-detect default).
- `[rpc]` — enable switch (controls Twisted import) + endpoint list.
- `[retry]` — enable / max_attempts / base+max delay / jitter / retryable retcodes.
- `[watchdog]` — enable / per-symbol timeout / scan interval / symbol filter.
- `[kelly]` — enable / default_fraction (0.5 = half-Kelly) / volume clamp + step.

#### EA (Cube.mq5 v6.06)

- Full V3.0 wire schema (Connected / Tick / HistoryTick / Bar / HistoryBar /
  Account / Position / Order with magic + comment).
- New `position_closed` / `order_removed` wire signals with `HistorySelect`
  lookups for accurate `close_price` / `profit` / `close_time` and
  `reason` ∈ {canceled, filled, expired, unknown}.
- TCP push via socket, line-delimited JSON; reconnect every `ReconnectSec`.

#### Examples

- `examples/run_bridge.py` — long-running subscriber demo (all event types).
- `examples/open_close_3.py` — open 3 BUY positions, then close them.
- `examples/place_cancel_order.py` — place a BUY_LIMIT, then cancel it.

#### Tests (82 / 82 passing)

- `test_dispatcher_state_machine.py` — open/modify/close + subscribe_all + dead-actor race.
- `test_executor_retry.py` — retryable retcode handling + max_attempts + stale rejection.
- `test_tick_watchdog.py` — per-symbol freshness + recovery + filter.
- `test_kelly.py` — formula + fraction override + clamps + invalid input.
- `test_rpc_protocol.py` — line decoding + partial frame + invalid JSON + oversize drop.
- `test_facade.py` — rpc on/off paths + subscribe before/after start + clean stop.
- `test_ea_decoder.py` — DECODER round-trip for all wire types.
- `test_mt5_client_preflight.py` — assert_trade_ready failure modes.
- `test_trade_commands.py` — OpenOrder defaults regression + validation.

### Notes

- Requires Python 3.12+, `MetaTrader5` 5.0+ (Windows-only via wheel).
- Optional `[rpc]` extra pulls in Twisted; no-RPC mode runs without it.
- All public callbacks fire synchronously on the dispatcher thread — keep them fast.

---

<!--
Version comparison links.
Populate the URLs once a remote (e.g. GitHub) is configured.
-->

[Unreleased]: https://github.com/worksduke/mt5-bridge/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/worksduke/mt5-bridge/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/worksduke/mt5-bridge/releases/tag/v0.1.0
