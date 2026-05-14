"""EAReceiver: lifecycle owner for the Twisted RPC subsystem.

Does NOT process messages — that's the protocol's job. This actor
exists purely to bind / unbind the TCP listeners deterministically as
part of the bridge's start / stop sequence.

Created only when ``rpc.enabled`` is True. Twisted is imported here
(lazily, via ``mt5_bridge.infra.rpc``) so the no-RPC mode never
touches Twisted.
"""
from __future__ import annotations

from typing import Any

import pykka

from mt5_bridge.infra.logging import get_logger

_log = get_logger("mt5_bridge.actors.ea_receiver")


class EAReceiver(pykka.ThreadingActor):
    use_daemon_thread = True

    def __init__(
        self,
        rpc_cfg: Any,                    # RpcConfig
        dispatcher_ref: pykka.ActorRef,
        reactor: Any,                    # twisted reactor (lazy-imported by facade)
    ) -> None:
        super().__init__()
        self._rpc_cfg = rpc_cfg
        self._dispatcher = dispatcher_ref
        self._reactor = reactor
        self._ports: tuple[Any, ...] = ()

    def on_start(self) -> None:
        from mt5_bridge.infra.rpc import install_ea_listeners
        # Reactor must be running for listenTCP to actually accept
        # connections, but install_ea_listeners just registers the
        # listener — the listener becomes live once reactor.run starts.
        # Hop the actual bind onto the reactor thread for thread-safety.
        ports_holder: list[Any] = []

        def _bind() -> None:
            ports_holder.extend(install_ea_listeners(
                self._rpc_cfg.endpoints,
                self._dispatcher,
                reactor=self._reactor,
            ))

        # If reactor is already running, hop on; otherwise the bind
        # happens immediately and the actual socket comes alive when
        # serve_forever() is called.
        if getattr(self._reactor, "running", False):
            self._reactor.callFromThread(_bind)
            # Wait briefly for the bind to land — best-effort, no
            # tight timing required.
            import time as _t
            _t.sleep(0.05)
        else:
            _bind()

        self._ports = tuple(ports_holder)
        _log.info("ea_receiver_started", port_count=len(self._ports))

    def on_stop(self) -> None:
        if not self._ports:
            return

        ports = self._ports
        self._ports = ()

        def _unbind() -> None:
            for port in reversed(ports):
                try:
                    port.stopListening()
                except Exception:
                    _log.exception("ea_port_stop_failed")

        if getattr(self._reactor, "running", False):
            self._reactor.callFromThread(_unbind)
        else:
            _unbind()

        _log.info("ea_receiver_stopped")


__all__ = ["EAReceiver"]
