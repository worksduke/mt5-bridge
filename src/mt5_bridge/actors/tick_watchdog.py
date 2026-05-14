"""TickWatchdog: per-symbol tick freshness monitor.

Receives ``Tick`` / ``HistoryTick`` from Dispatcher; tracks
``last_tick_time_ms`` per symbol; emits:
    - ``SymbolStale``       → Executor (rejects new entry orders for this symbol)
    - ``EmergencyTickStale``→ Dispatcher (broadcast to user callbacks)
    - ``SymbolAlive``       → Executor (re-enables entry orders)

Uses ``threading.Timer`` (not ``reactor.callLater``) so it works in
both rpc-on and rpc-off modes without Twisted as a dependency. The
Timer thread doesn't touch our state directly — it ``tell``s the
actor a ``_ScanTick`` so all mutation stays on the actor thread.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import pykka

from mt5_bridge.contracts.ea_messages import HistoryTick, Tick
from mt5_bridge.contracts.internal_messages import SymbolAlive, SymbolStale
from mt5_bridge.contracts.output_events import EmergencyTickStale
from mt5_bridge.infra.logging import get_logger

_log = get_logger("mt5_bridge.actors.tick_watchdog")


def _now_ms() -> int:
    return int(time.time() * 1000)


class _ScanTick:
    """Internal wake-up the Timer thread sends to trigger a scan."""
    __slots__ = ()


class TickWatchdog(pykka.ThreadingActor):
    use_daemon_thread = True

    def __init__(
        self,
        dispatcher_ref: pykka.ActorRef,
        cfg: Any,                                    # WatchdogConfig
    ) -> None:
        super().__init__()
        self._dispatcher = dispatcher_ref
        self._executor_ref: pykka.ActorRef | None = None
        self._cfg = cfg
        self._last_ms: dict[str, int] = {}
        self._stale: set[str] = set()
        self._timer: threading.Timer | None = None
        # Pre-seed last_ms for configured symbols using start time as
        # the deadline anchor so they don't all instantly look stale.
        anchor = _now_ms()
        for s in (cfg.symbols or ()):
            self._last_ms[s] = anchor

    # ── proxy-callable wiring API ─────────────────────────────
    def set_executor_ref(self, ref: pykka.ActorRef | None) -> None:
        self._executor_ref = ref

    # ── lifecycle ─────────────────────────────────────────────
    def on_start(self) -> None:
        self._schedule_next()

    def on_stop(self) -> None:
        if self._timer is not None:
            self._timer.cancel()

    # ── pykka entry point ─────────────────────────────────────
    def on_receive(self, message: object) -> None:
        if isinstance(message, (Tick, HistoryTick)):
            sym = message.symbol
            if self._cfg.symbols and sym not in self._cfg.symbols:
                return
            self._last_ms[sym] = _now_ms()
            if sym in self._stale:
                self._stale.discard(sym)
                _log.info("symbol_recovered", symbol=sym)
                self._safe_tell(self._executor_ref, SymbolAlive(
                    symbol=sym, tick_time_ms=message.time_msc,
                ))
            return

        if isinstance(message, _ScanTick):
            self._do_scan()
            return

    # ── periodic scan ─────────────────────────────────────────
    def _schedule_next(self) -> None:
        self._timer = threading.Timer(self._cfg.check_interval_seconds, self._wake)
        self._timer.daemon = True
        self._timer.start()

    def _wake(self) -> None:
        try:
            self.actor_ref.tell(_ScanTick())
        except pykka.ActorDeadError:
            return

    def _do_scan(self) -> None:
        now = _now_ms()
        timeout_ms = int(self._cfg.tick_timeout_seconds * 1000)
        for sym, last in self._last_ms.items():
            silence_ms = now - last
            if silence_ms < timeout_ms or sym in self._stale:
                continue
            self._stale.add(sym)
            silence_seconds = silence_ms / 1000.0
            _log.warning(
                "symbol_stale",
                symbol=sym,
                silence_seconds=silence_seconds,
                timeout_seconds=self._cfg.tick_timeout_seconds,
            )
            self._safe_tell(self._executor_ref, SymbolStale(
                symbol=sym,
                last_tick_time_ms=last,
                silence_seconds=silence_seconds,
            ))
            self._safe_tell(self._dispatcher, EmergencyTickStale(
                symbol=sym,
                last_tick_time_ms=last,
                silence_seconds=silence_seconds,
                detected_at_ms=now,
            ))
        self._schedule_next()

    @staticmethod
    def _safe_tell(ref: pykka.ActorRef | None, msg: object) -> None:
        """Forward ``msg`` to ``ref``, swallowing dead-actor races
        that happen during ``bridge.stop()``."""
        if ref is None:
            return
        try:
            ref.tell(msg)
        except pykka.ActorDeadError:
            pass


__all__ = ["TickWatchdog"]
