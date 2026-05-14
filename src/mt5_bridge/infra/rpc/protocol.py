"""Twisted ``LineReceiver`` protocol for the EA push channel.

Wire format
-----------
- One JSON object per line.
- Lines are framed by ``RpcEndpoint.delimiter`` (default ``b"\\n"``).
- The JSON object MUST carry a ``"type"`` discriminator
  (see ``mt5_bridge.contracts.ea_messages.EAMessage``).
- Each connection is one EA bridge for one symbol; the EA never
  receives data back, so we only override ``lineReceived``.

Reactor-thread bound: every method here runs on Twisted's single
reactor thread, so the dispatcher's ``ref.tell`` calls cross the
Pykka thread boundary (``tell`` is thread-safe by Pykka design).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import msgspec
from twisted.internet import protocol
from twisted.protocols.basic import LineReceiver

from mt5_bridge.contracts.ea_messages import DECODER
from mt5_bridge.infra.logging import get_logger

if TYPE_CHECKING:
    import pykka

    from mt5_bridge.configs.bridge import RpcEndpoint

_log = get_logger("mt5_bridge.infra.rpc.protocol")


class EAProtocol(LineReceiver):
    """One TCP connection from one EA bridge.

    Inherits Twisted's bytes-mode ``LineReceiver`` so framing runs at
    C speed. Per-instance ``delimiter`` and ``MAX_LENGTH`` are
    overwritten in ``connectionMade`` from the owning factory's
    ``RpcEndpoint`` so two listeners on different ports can have
    different framing without sharing class state.
    """

    delimiter:  bytes = b"\n"
    MAX_LENGTH: int   = 1 * 1024 * 1024

    factory: "EAFactory"  # narrowed for the type-checker

    # ── lifecycle ─────────────────────────────────────────────
    def connectionMade(self) -> None:
        cfg = self.factory.cfg

        # Per-connection framing (NOT per-class).
        self.delimiter  = cfg.delimiter
        self.MAX_LENGTH = cfg.max_length

        peer = self.transport.getPeer()  # type: ignore[union-attr,misc]
        self._peer = f"{peer.host}:{peer.port}"

        # Low-latency push channel. Guarded for non-TCP transports
        # (e.g. ``StringTransport`` in unit tests) that lack these.
        for tweak in ("setTcpNoDelay", "setTcpKeepAlive"):
            fn = getattr(self.transport, tweak, None)
            if callable(fn):
                fn(True)

        _log.info(
            "ea_connected",
            symbol=cfg.symbol,
            peer=self._peer,
            port=cfg.port,
        )

    def connectionLost(self, reason: Any = protocol.connectionDone) -> None:
        cfg = getattr(self.factory, "cfg", None)
        _log.info(
            "ea_disconnected",
            symbol=getattr(cfg, "symbol", "?"),
            peer=getattr(self, "_peer", "?"),
            port=getattr(cfg, "port", -1),
            reason=reason.getErrorMessage(),
        )

    # ── data ──────────────────────────────────────────────────
    def lineReceived(self, line: bytes) -> None:
        cfg = self.factory.cfg

        try:
            data = DECODER.decode(line)
        except msgspec.ValidationError as e:
            # Schema mismatch — most likely an EA wire-format change.
            # Drop the line, keep the connection (high-volume stream
            # must not stop on a single malformed frame).
            _log.error(
                "ea_decode_failed",
                symbol=cfg.symbol,
                peer=getattr(self, "_peer", "?"),
                error=str(e),
                line_preview=line[:200].decode("utf-8", errors="replace"),
            )
            return
        except Exception as e:  # pragma: no cover - defensive
            _log.error(
                "ea_decode_failed",
                symbol=cfg.symbol,
                peer=getattr(self, "_peer", "?"),
                error=repr(e),
                line_preview=line[:200].decode("utf-8", errors="replace"),
            )
            return

        # Hand off to the dispatcher actor. Pykka's ``tell`` is
        # thread-safe by design, so calling it from the reactor
        # thread is fine.
        try:
            self.factory.dispatcher_ref.tell(data)
        except Exception as e:  # pragma: no cover - defensive
            _log.exception(
                "ea_dispatch_failed",
                symbol=cfg.symbol,
                peer=getattr(self, "_peer", "?"),
                error=repr(e),
                msg_type=type(data).__name__,
            )

    def lineLengthExceeded(self, line: bytes) -> None:
        """Drop the connection on an over-long frame.

        A malformed sender pushing a giant line could otherwise tie up
        the reactor's read buffer indefinitely.
        """
        cfg = self.factory.cfg
        _log.warning(
            "ea_line_too_long",
            symbol=cfg.symbol,
            peer=getattr(self, "_peer", "?"),
            limit=self.MAX_LENGTH,
        )
        self.transport.loseConnection()  # type: ignore[union-attr,misc]


class EAFactory(protocol.Factory):
    """One factory per ``RpcEndpoint``.

    Holds the cfg (per-port framing) and the dispatcher ActorRef so
    each spawned protocol can hand decoded messages to it.
    """

    def __init__(
        self,
        cfg: "RpcEndpoint",
        dispatcher_ref: "pykka.ActorRef",
    ) -> None:
        self.cfg = cfg
        self.dispatcher_ref = dispatcher_ref

    # NOTE: Twisted's stubs declare a private ``_ProtoWithFactory | None``
    # return type that we can't reference by name. Returning EAProtocol
    # (a Protocol subclass with a ``factory`` attribute) satisfies the
    # structural contract — this ``ignore[override]`` is the standard
    # pattern for Factory subclasses.
    def buildProtocol(self, addr: object) -> EAProtocol:  # type: ignore[override]
        p = EAProtocol()
        p.factory = self
        return p


__all__ = ["EAFactory", "EAProtocol"]
