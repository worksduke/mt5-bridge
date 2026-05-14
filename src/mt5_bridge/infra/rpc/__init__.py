"""Twisted+msgspec RPC subsystem for the EA push channel.

Lazy-loaded by ``MT5Bridge.start_io`` only when ``rpc.enabled`` is True
so projects that never use RPC do not pay for importing Twisted.

Public surface:

    EAFactory / EAProtocol — the LineReceiver implementation
    install_ea_listeners   — bind one TCP listener per RpcEndpoint
"""
from mt5_bridge.infra.rpc.protocol import EAFactory, EAProtocol
from mt5_bridge.infra.rpc.server import install_ea_listeners

__all__ = [
    "EAFactory",
    "EAProtocol",
    "install_ea_listeners",
]
