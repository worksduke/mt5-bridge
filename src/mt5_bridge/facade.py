"""``MT5Bridge`` — single public façade.

Two typical usages:

    # (A) Built-in RPC (Twisted reactor on main thread)
    bridge = MT5Bridge("config.toml")
    bridge.subscribe(EventType.POSITION_OPENED, on_opened)
    bridge.subscribe(Tick, on_tick)
    bridge.start_io()
    bridge.serve_forever()              # blocks; ctrl+c → bridge.stop()

    # (B) External feed, RPC disabled (no Twisted dependency)
    bridge = MT5Bridge("config_no_rpc.toml")
    bridge.subscribe(EventType.POSITION_OPENED, on_opened)
    bridge.start_io()
    while True:
        msg = my_external_source.recv()  # parses to an EAMessage
        bridge.feed_input(msg)

The actors are launched in dependency order during ``start_io``;
subscriptions made before ``start_io`` are buffered and flushed
afterwards. ``stop`` tears everything down in reverse.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pykka

from mt5_bridge.actors.dispatcher import Dispatcher
from mt5_bridge.actors.executor import Executor
from mt5_bridge.actors.kelly_actor import KellyActor
from mt5_bridge.actors.tick_watchdog import TickWatchdog
from mt5_bridge.configs.bridge import BridgeConfig, load_bridge_config
from mt5_bridge.configs.mt5 import MT5Config, load_mt5_config
from mt5_bridge.contracts.trade_commands import (
    CancelOrder,
    ClosePosition,
    ModifyOrder,
    ModifyPosition,
    OpenOrder,
    OpenPosition,
)
from mt5_bridge.infra.logging import get_logger
from mt5_bridge.infra.mt5_client import MT5Client
from mt5_bridge.infra.scheduler import ReactorScheduler, TimerScheduler

_log = get_logger("mt5_bridge.facade")


class MT5Bridge:
    def __init__(self, config_path: str | Path) -> None:
        self._mt5_cfg:    MT5Config    = load_mt5_config(config_path)
        self._bridge_cfg: BridgeConfig = load_bridge_config(config_path)
        self._client = MT5Client(self._mt5_cfg)

        self._dispatcher: pykka.ActorRef | None = None
        self._executor:   pykka.ActorRef | None = None
        self._watchdog:   pykka.ActorRef | None = None
        self._kelly:      pykka.ActorRef | None = None
        self._receiver:   pykka.ActorRef | None = None
        self._scheduler: Any = None
        self._reactor:   Any = None

        # Subscriptions made before start_io are buffered here.
        # Each entry: ("one", key, cb) | ("all", None, cb)
        self._pending_subs: list[tuple[str, Any, Callable[[Any], None]]] = []

    # ── public subscription API ───────────────────────────────
    def subscribe(self, key: Any, cb: Callable[[Any], None]) -> None:
        """Register a callback for one event class or one EventType value."""
        if self._dispatcher is None:
            self._pending_subs.append(("one", key, cb))
        else:
            self._dispatcher.proxy().register_callback(key, cb).get()

    def subscribe_all(self, cb: Callable[[Any], None]) -> None:
        """Register a catch-all callback receiving every broadcast event."""
        if self._dispatcher is None:
            self._pending_subs.append(("all", None, cb))
        else:
            self._dispatcher.proxy().register_callback_all(cb).get()

    # ── public input API ──────────────────────────────────────
    def feed_input(self, msg: Any) -> None:
        """Inject a parsed EA message directly into the dispatcher.

        Available regardless of ``rpc.enabled`` — used by callers
        that maintain their own IO and only want the bridge's
        dispatch / state machine.
        """
        if self._dispatcher is None:
            raise RuntimeError("bridge not started — call start_io() first")
        self._dispatcher.tell(msg)

    # ── public Kelly access ───────────────────────────────────
    @property
    def kelly(self) -> pykka.ActorProxy:
        """Proxy onto the KellyActor for synchronous ``suggest_volume(...)`` calls.

        Raises:
            RuntimeError: if Kelly was disabled in config or bridge not started.
        """
        if self._kelly is None:
            raise RuntimeError("kelly disabled or bridge not started")
        return self._kelly.proxy()

    # ── public command API (fire-and-forget) ──────────────────
    def open_position(self,   c: OpenPosition)   -> None: self._tell_executor(c)
    def close_position(self,  c: ClosePosition)  -> None: self._tell_executor(c)
    def modify_position(self, c: ModifyPosition) -> None: self._tell_executor(c)
    def open_order(self,      c: OpenOrder)      -> None: self._tell_executor(c)
    def modify_order(self,    c: ModifyOrder)    -> None: self._tell_executor(c)
    def cancel_order(self,    c: CancelOrder)    -> None: self._tell_executor(c)

    def _tell_executor(self, cmd: Any) -> None:
        if self._executor is None:
            raise RuntimeError("bridge not started — call start_io() first")
        self._executor.tell(cmd)

    # ── lifecycle ─────────────────────────────────────────────
    def start_io(self) -> None:
        """Initialize MT5, start every actor, install RPC listeners.

        When ``rpc.enabled`` the listeners are bound but only become
        live once ``serve_forever()`` runs the reactor. Subscriptions
        buffered before this call are flushed at the end.
        """
        if not self._client.initialize():
            raise RuntimeError("MT5 initialize failed")

        # Fail fast if the terminal / account can't accept trades
        # (AutoTrading off, account locked, EAs disabled). Roll back
        # the SDK init so the user can retry once they've fixed it.
        try:
            self._client.assert_trade_ready()
        except RuntimeError:
            self._client.shutdown()
            raise

        self._dispatcher = Dispatcher.start()

        if self._bridge_cfg.kelly.enabled:
            self._kelly = KellyActor.start(self._client, self._bridge_cfg.kelly)
            self._dispatcher.proxy().set_kelly_ref(self._kelly).get()

        if self._bridge_cfg.watchdog.enabled:
            self._watchdog = TickWatchdog.start(
                self._dispatcher, self._bridge_cfg.watchdog,
            )
            self._dispatcher.proxy().set_watchdog_ref(self._watchdog).get()

        if self._bridge_cfg.rpc.enabled:
            from twisted.internet import reactor as _reactor
            self._reactor = _reactor
            self._scheduler = ReactorScheduler(_reactor)
        else:
            self._scheduler = TimerScheduler()

        self._executor = Executor.start(
            self._client, self._dispatcher,
            self._scheduler, self._bridge_cfg.retry,
        )

        if self._watchdog is not None:
            self._watchdog.proxy().set_executor_ref(self._executor).get()

        if self._bridge_cfg.rpc.enabled:
            from mt5_bridge.actors.ea_receiver import EAReceiver
            self._receiver = EAReceiver.start(
                self._bridge_cfg.rpc, self._dispatcher, self._reactor,
            )

        for kind, k, cb in self._pending_subs:
            if kind == "one":
                self._dispatcher.proxy().register_callback(k, cb).get()
            else:
                self._dispatcher.proxy().register_callback_all(cb).get()
        self._pending_subs.clear()

        _log.info(
            "bridge_started",
            rpc_enabled=self._bridge_cfg.rpc.enabled,
            retry_enabled=self._bridge_cfg.retry.enabled,
            watchdog_enabled=self._bridge_cfg.watchdog.enabled,
            kelly_enabled=self._bridge_cfg.kelly.enabled,
        )

    def serve_forever(self) -> None:
        """Run the Twisted reactor on the current (must be main) thread.

        Blocks until ``stop()`` (or a signal handler) calls
        ``reactor.stop``. Only valid when ``rpc.enabled``; in
        no-RPC mode the user owns their own loop.
        """
        if not self._bridge_cfg.rpc.enabled:
            raise RuntimeError("rpc disabled — run your own loop instead")
        if self._reactor is None:
            raise RuntimeError("bridge not started — call start_io() first")
        self._reactor.run()

    def stop(self) -> None:
        """Tear down in reverse order of start_io.

        Safe to call multiple times. After a clean stop the bridge
        instance can be reused with ``start_io()`` again.

        Order matters: actor on_stop callbacks (e.g. EAReceiver
        unbinding TCP ports) schedule work on the reactor via
        ``callFromThread``. We stop the actors FIRST so those queued
        callbacks land in the reactor queue before ``reactor.stop``,
        guaranteeing they actually run.
        """
        for ref in (self._receiver, self._executor, self._watchdog,
                    self._kelly, self._dispatcher):
            if ref is None:
                continue
            try:
                ref.stop()
            except Exception:
                _log.exception("actor_stop_failed", actor=str(ref))

        if self._reactor is not None and getattr(self._reactor, "running", False):
            self._reactor.callFromThread(self._reactor.stop)

        self._receiver = self._executor = self._watchdog = None
        self._kelly = self._dispatcher = None
        self._scheduler = None
        self._reactor = None

        try:
            self._client.shutdown()
        except Exception:
            _log.exception("mt5_shutdown_failed")

        _log.info("bridge_stopped")


__all__ = ["MT5Bridge"]
