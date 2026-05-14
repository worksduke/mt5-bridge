"""High-level installer: bind one TCP listener per ``RpcEndpoint``.

``install_ea_listeners`` does NOT call ``reactor.run()`` and it does
NOT call ``reactor.stop()`` either. It is the caller's job to own the
reactor lifecycle (``MT5Bridge.serve_forever`` runs it,
``MT5Bridge.stop`` stops it). The function only ``listenTCP``\\s and
returns the bound ports so the caller can stop them deterministically.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any, cast

from mt5_bridge.infra.logging import get_logger
from mt5_bridge.infra.rpc.protocol import EAFactory

if TYPE_CHECKING:
    import pykka
    from twisted.internet.interfaces import IListeningPort, IReactorTCP

    from mt5_bridge.configs.bridge import RpcEndpoint

_log = get_logger("mt5_bridge.infra.rpc.server")


def install_ea_listeners(
    endpoints: Iterable["RpcEndpoint"],
    dispatcher_ref: "pykka.ActorRef",
    *,
    reactor: "IReactorTCP | None" = None,
) -> Sequence["IListeningPort"]:
    """Bind one TCP listener per ``RpcEndpoint``.

    Args:
        endpoints:      The ``RpcEndpoint``\\s to bind. Empty -> no-op.
        dispatcher_ref: The Pykka ActorRef every spawned protocol will
                        ``tell`` decoded frames to.
        reactor:        Twisted reactor to bind on. ``None`` (default)
                        uses ``twisted.internet.reactor``.

    Returns:
        Tuple of bound ``IListeningPort``\\s in input order.
    """
    if reactor is None:
        from twisted.internet import reactor as default_reactor
        reactor = default_reactor  # type: ignore[assignment]

    assert reactor is not None  # for mypy

    ports: list[IListeningPort] = []
    for cfg in endpoints:
        factory = EAFactory(cfg, dispatcher_ref)
        port: IListeningPort = cast(Any, reactor).listenTCP(
            cfg.port,
            factory,
            interface=cfg.host,
        )
        ports.append(port)

        bound = cast(Any, port).getHost()
        _log.info(
            "ea_listener_started",
            symbol=cfg.symbol,
            host=cfg.host,
            requested_port=cfg.port,
            bound_port=getattr(bound, "port", cfg.port),
        )

    _log.info("ea_listeners_ready", count=len(ports))
    return tuple(ports)


__all__ = ["install_ea_listeners"]
