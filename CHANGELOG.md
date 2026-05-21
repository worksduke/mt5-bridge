# Changelog

All notable changes to **mt5-bridge** (*pykka + msgspec event bridge for MetaTrader 5*) will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

_No unreleased changes yet. New entries should be filed under one of:_
_`Added` / `Changed` / `Deprecated` / `Removed` / `Fixed` / `Security`._

---

## [0.3.1] - 2026-05-21

Live-test bugfix following v0.3.0. The wire types and dispatcher were
correct, but the two built-in demos forgot about them — running the CLI
to verify CubeAll EA showed no cube output.

### Fixed

- `mt5-bridge run` CLI (`src/mt5_bridge/__main__.py`) and
  `examples/run_bridge.py` were missing `Cube` / `MetaCube` subscribers
  since v0.3.0. Both now print:
  - `[CUBE~]` / `[META~]` — live forming-cube stream (raw class subscribe,
    gated on `is_closed=False`).
  - `[CUBE✓]` / `[META✓]` — finalization via `EventType.CUBE_CLOSED` /
    `META_CUBE_CLOSED`, including the closed-only `efficiency` /
    `obv_score` metrics and the `is_history` flag.

### Changed

- README: added a `Cube` / `MetaCube` 实时 vs 收线 callout under the
  OutputEvent table clarifying that `bridge.subscribe(Cube, cb)` picks
  up the live forming stream while `EventType.CUBE_CLOSED` fires once
  per finalization — mirrors the existing `Bar` / `BarClosed` idiom.

---

## [0.3.0] - 2026-05-20

Wire-schema expansion: bridge now recognizes the Cube / MetaCube
directional-block message family pushed by `CubeAll.mq5` (the new EA build
that supersedes `Cube.mq5` v6.06). Strictly additive — no existing public
API removed or changed.

### Added

- **6 new EA wire types** in `contracts/ea_messages.py`:
  - `Cube`, `HistoryCube`, `HistoryCubeDone`
  - `MetaCube`, `HistoryMetaCube`, `HistoryMetaCubeDone`

  All follow the existing `msgspec.Struct(tag=..., tag_field="type", frozen=True)`
  pattern and are added to the `EAMessage` union so `DECODER` picks them up
  automatically. `dir` ∈ {`UP`, `DOWN`, `RANGE`, `NONE`}; `state` ∈
  {`FORMING`, `ACTIVE`, `AT_RISK`, `DEAD`}.

- **2 new OutputEvents** in `contracts/output_events.py`:
  - `CubeClosed`  → `EventType.CUBE_CLOSED = 41`
  - `MetaCubeClosed` → `EventType.META_CUBE_CLOSED = 42`

  Mirrors the v0.2 `Bar → BarClosed` pattern: dispatcher emits these on
  every `is_closed=True` Cube/MetaCube (live or history-replay), with an
  `is_history` flag distinguishing the two. Raw `Cube`/`MetaCube` class
  subscriptions still receive the full live stream (forming + closed).

- **Dispatcher branches** in `actors/dispatcher.py`:
  - 4 new `isinstance` branches for `Cube` / `HistoryCube` / `MetaCube` /
    `HistoryMetaCube`, plus `_emit_cube_closed` / `_emit_meta_cube_closed`
    private helpers next to the existing `_emit_bar_closed`.
  - `HistoryCubeDone` / `HistoryMetaCubeDone` fall through to the catch-all
    broadcast (matching how `HistoryBarDone` / `HistoryTickDone` behave).

- **16 new tests** (7 decoder round-trips + 9 dispatcher fan-out).
  Total suite is now **109 / 109 passing** (was 93).

### Changed

- Bumped `__version__` to `0.3.0` (single source in
  `src/mt5_bridge/__init__.py`; `pyproject.toml` reads it dynamically).
- `README.md` OutputEvent table and CLI quickstart updated with the new
  `CubeClosed` / `MetaCubeClosed` events.
- `ea/README.md` rewritten for the new EA (`CubeAll`) and protocol
  expansion.

### Removed

- `ea/Cube.mq5` (source — author no longer publishes EA source).
- `ea/Cube.ex5` (old compiled binary — does not push the new cube/meta
  messages; misleading to leave). The replacement `CubeAll.ex5` will be
  dropped into `ea/` by the EA author as a follow-up commit after the
  Python side is verified.

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

[Unreleased]: https://github.com/worksduke/mt5-bridge/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/worksduke/mt5-bridge/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/worksduke/mt5-bridge/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/worksduke/mt5-bridge/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/worksduke/mt5-bridge/releases/tag/v0.1.0
