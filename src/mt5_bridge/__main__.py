"""mt5-bridge CLI: ``mt5-bridge run --config config.toml``.

Entry point exposed via ``[project.scripts]`` so ``pip install -e .``
puts a ``mt5-bridge`` console script on PATH. Currently exposes one
sub-command:

    mt5-bridge run --config config.toml

The ``run`` command starts the bridge, subscribes a single-line stdout
printer to every event type, and (in RPC mode) blocks the main thread
on Twisted's reactor until SIGINT / SIGTERM.

Not a replacement for ``examples/run_bridge.py`` — that file shows
the long-form, hand-wired equivalent for users who want to fork it.
"""
from __future__ import annotations

import argparse
import signal
import sys
import time

from mt5_bridge import (
    Account,
    Connected,
    EmergencyTickStale,
    EventType,
    MT5Bridge,
    Tick,
    __version__,
)
from mt5_bridge.configs.bridge import load_bridge_config


def _wire_print_handlers(bridge: MT5Bridge) -> None:
    """Subscribe a single-line stdout printer to every event type."""
    bridge.subscribe(Connected, lambda c: print(f"[EA]    connected — {c.symbol}"))
    bridge.subscribe(Tick, lambda t: print(f"[TICK]  {t.symbol} bid={t.bid} ask={t.ask}"))
    bridge.subscribe(Account, lambda a: print(
        f"[ACCT]  equity={a.equity} margin={a.margin} profit={a.profit}"))
    bridge.subscribe(EventType.POSITION_OPENED, lambda e: print(
        f"[OPEN]  {e.ticket} {e.pos_type} {e.symbol} vol={e.volume} @ {e.open_price}"))
    bridge.subscribe(EventType.POSITION_MODIFIED, lambda e: print(
        f"[MOD]   {e.ticket} sl={e.sl} tp={e.tp}"))
    bridge.subscribe(EventType.POSITION_CLOSED, lambda e: print(
        f"[CLOSE] {e.ticket} {e.pos_type} {e.symbol} close={e.close_price} profit={e.profit}"))
    bridge.subscribe(EventType.ORDER_PLACED, lambda e: print(
        f"[ORD+]  {e.ticket} {e.order_type} {e.symbol} @ {e.open_price}"))
    bridge.subscribe(EventType.ORDER_MODIFIED, lambda e: print(
        f"[ORD~]  {e.ticket} price={e.open_price}"))
    bridge.subscribe(EventType.ORDER_CANCELED, lambda e: print(
        f"[ORD-]  {e.ticket} reason={e.reason}"))
    bridge.subscribe(EventType.BAR_CLOSED, lambda b: print(
        f"[BAR]   {b.symbol} {b.role}/{b.tf_period} O={b.open} H={b.high} "
        f"L={b.low} C={b.close} V={b.volume}"))
    bridge.subscribe(EmergencyTickStale, lambda e: print(
        f"[!!]    STALE {e.symbol} silent={e.silence_seconds:.1f}s"))


def cmd_run(args: argparse.Namespace) -> int:
    bridge = MT5Bridge(args.config)
    _wire_print_handlers(bridge)
    bridge.start_io()

    def _shutdown(*_args: object) -> None:
        print("\n[SHUTDOWN] stopping bridge…")
        bridge.stop()

    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)

    cfg = load_bridge_config(args.config)
    if cfg.rpc.enabled:
        print("[RUN] reactor running, Ctrl+C to stop…")
        bridge.serve_forever()
    else:
        print("[RUN] no-RPC mode — feed_input from your own loop. Sleeping; Ctrl+C to stop.")
        while True:
            time.sleep(60)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mt5-bridge",
        description="Pykka + msgspec event bridge for MetaTrader 5",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser(
        "run",
        help="Run the bridge, subscribe to all events, print to stdout",
    )
    p_run.add_argument("--config", "-c", required=True, help="Path to config.toml")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
