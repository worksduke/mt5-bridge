# Changelog

All notable changes to **mt5-bridge** (*pykka + msgspec event bridge for MetaTrader 5*) will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

_No unreleased changes yet. New entries should be filed under one of:_
_`Added` / `Changed` / `Deprecated` / `Removed` / `Fixed` / `Security`._

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

[Unreleased]: about:blank
[0.1.0]: about:blank
