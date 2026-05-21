"""Demo: run MT5Bridge in either RPC mode or feed-input mode.

Usage:
    python examples/run_bridge.py config.toml          # uses [rpc] from config
    python examples/run_bridge.py config_no_rpc.toml   # external feed mode

When ``[rpc] enabled = true`` we call ``bridge.serve_forever()`` which
runs Twisted's reactor on the main thread (Ctrl+C → graceful stop).
When disabled, the demo just sleeps so you can call
``bridge.feed_input(...)`` from elsewhere (or extend this script).
"""
from __future__ import annotations

import signal
import sys
import time

from mt5_bridge import (
    Account,
    Connected,
    Cube,
    CubeClosed,
    EmergencyTickStale,
    EventType,
    MetaCube,
    MetaCubeClosed,
    MT5Bridge,
    OrderCanceled,
    OrderModified,
    OrderPlaced,
    PositionClosed,
    PositionModified,
    PositionOpened,
    Tick,
)
from mt5_bridge.configs.bridge import load_bridge_config


def on_connected(c: Connected) -> None:
    print(f"[EA]   connected — symbol={c.symbol} time={c.time}")


def on_tick(t: Tick) -> None:
    print(f"[TICK] {t.symbol} bid={t.bid} ask={t.ask}")


def on_account(a: Account) -> None:
    print(f"[ACCT] equity={a.equity} margin={a.margin} profit={a.profit}")


def on_position_opened(e: PositionOpened) -> None:
    print(f"[OPEN] ticket={e.ticket} {e.pos_type} {e.symbol} vol={e.volume} "
          f"@ {e.open_price} magic={e.magic}")


def on_position_modified(e: PositionModified) -> None:
    print(f"[MOD]  ticket={e.ticket} sl={e.sl} tp={e.tp}")


def on_position_closed(e: PositionClosed) -> None:
    print(f"[CLOSE] ticket={e.ticket} {e.pos_type} {e.symbol} "
          f"open={e.open_price} close={e.close_price} profit={e.profit}")


def on_order_placed(e: OrderPlaced) -> None:
    print(f"[ORD+] ticket={e.ticket} {e.order_type} {e.symbol} "
          f"vol={e.volume} @ {e.open_price}")


def on_order_modified(e: OrderModified) -> None:
    print(f"[ORD~] ticket={e.ticket} price={e.open_price} sl={e.sl} tp={e.tp}")


def on_order_canceled(e: OrderCanceled) -> None:
    print(f"[ORD-] ticket={e.ticket} {e.order_type} {e.symbol} "
          f"reason={e.reason}")


def on_cube_forming(c: Cube) -> None:
    # The CubeClosed OutputEvent below covers finalization; here we only
    # surface the live forming-cube stream the EA pushes per-tick.
    if c.is_closed:
        return
    print(f"[CUBE~] {c.symbol} {c.role}/{c.tf_period} #{c.id} {c.dir} "
          f"state={c.state} bars={c.bar_count}")


def on_cube_closed(c: CubeClosed) -> None:
    print(f"[CUBE✓] {c.symbol} {c.role}/{c.tf_period} #{c.id} {c.dir} "
          f"bars={c.bar_count} eff={c.efficiency:.2f} obv={c.obv_score:+.2f} "
          f"{'(history)' if c.is_history else '(live)'}")


def on_meta_forming(m: MetaCube) -> None:
    if m.is_closed:
        return
    print(f"[META~] {m.symbol} {m.role}/{m.tf_period} #{m.id} {m.dir} "
          f"state={m.state} cubes={m.cube_count} bars={m.bar_count}")


def on_meta_closed(m: MetaCubeClosed) -> None:
    print(f"[META✓] {m.symbol} {m.role}/{m.tf_period} #{m.id} {m.dir} "
          f"cubes={m.cube_count} bars={m.bar_count} eff={m.efficiency:.2f} "
          f"obv={m.obv_score:+.2f} {'(history)' if m.is_history else '(live)'}")


def on_emergency(e: EmergencyTickStale) -> None:
    print(
        f"[!!]   TICK STALE on {e.symbol}: silent for {e.silence_seconds:.1f}s "
        f"— please check open positions manually"
    )


def main(config_path: str) -> int:
    bridge = MT5Bridge(config_path)

    bridge.subscribe(Connected, on_connected)
    bridge.subscribe(Tick, on_tick)
    bridge.subscribe(Account, on_account)
    bridge.subscribe(EventType.POSITION_OPENED,   on_position_opened)
    bridge.subscribe(EventType.POSITION_MODIFIED, on_position_modified)
    bridge.subscribe(EventType.POSITION_CLOSED,   on_position_closed)
    bridge.subscribe(EventType.ORDER_PLACED,      on_order_placed)
    bridge.subscribe(EventType.ORDER_MODIFIED,    on_order_modified)
    bridge.subscribe(EventType.ORDER_CANCELED,    on_order_canceled)
    bridge.subscribe(Cube,                         on_cube_forming)
    bridge.subscribe(EventType.CUBE_CLOSED,        on_cube_closed)
    bridge.subscribe(MetaCube,                     on_meta_forming)
    bridge.subscribe(EventType.META_CUBE_CLOSED,   on_meta_closed)
    bridge.subscribe(EmergencyTickStale, on_emergency)

    bridge.start_io()

    # Graceful Ctrl+C: just stop the bridge. bridge.stop() schedules
    # reactor.stop on the reactor thread, so serve_forever() will return
    # cleanly. Calling sys.exit here would raise SystemExit through the
    # reactor's select() and Twisted would log an "Unexpected error in
    # main loop" traceback.
    def _shutdown(*_args):
        print("\n[SHUTDOWN] stopping bridge…")
        bridge.stop()

    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)

    cfg = load_bridge_config(config_path)
    if cfg.rpc.enabled:
        print("[RUN] reactor running, Ctrl+C to stop…")
        bridge.serve_forever()
    else:
        print("[RUN] no-RPC mode — call bridge.feed_input(msg) from elsewhere.")
        print("      Sleeping forever; Ctrl+C to stop.")
        while True:
            time.sleep(60)

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"usage: python {sys.argv[0]} <config.toml>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
