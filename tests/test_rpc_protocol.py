"""EAProtocol unit tests using Twisted's StringTransport.

No reactor is started — we drive ``dataReceived`` directly and assert
the dispatcher receives the right decoded messages. The dispatcher
in these tests is a fake ``ActorRef`` that records every ``tell``.
"""
from __future__ import annotations

import msgspec
import pytest
from twisted.internet.testing import StringTransport

from mt5_bridge.configs.bridge import RpcEndpoint
from mt5_bridge.contracts.ea_messages import Account, Tick
from mt5_bridge.infra.rpc.protocol import EAFactory, EAProtocol


class FakeRef:
    """Stand-in for pykka.ActorRef — just records what's told to it."""
    def __init__(self):
        self.received = []

    def tell(self, msg):
        self.received.append(msg)


@pytest.fixture
def proto():
    cfg = RpcEndpoint(symbol="TEST", host="127.0.0.1", port=0,
                      delimiter=b"\n", max_length=1024)
    ref = FakeRef()
    factory = EAFactory(cfg, ref)
    p: EAProtocol = factory.buildProtocol(("127.0.0.1", 0))
    transport = StringTransport()
    p.makeConnection(transport)
    return p, ref


def _tick_line(symbol="X", bid=1.0):
    t = Tick(symbol=symbol, time="2024.01.01 00:00:00", time_msc=1,
             bid=bid, ask=bid + 0.1, last=0.0, volume=1)
    return msgspec.json.encode(t) + b"\n"


def test_single_tick_decoded(proto):
    p, ref = proto
    p.dataReceived(_tick_line("XAUUSD", 2050.0))
    assert len(ref.received) == 1
    assert isinstance(ref.received[0], Tick)
    assert ref.received[0].symbol == "XAUUSD"
    assert ref.received[0].bid == 2050.0


def test_multiple_lines_in_one_chunk(proto):
    p, ref = proto
    p.dataReceived(_tick_line("A", 1.0) + _tick_line("B", 2.0) + _tick_line("C", 3.0))
    assert len(ref.received) == 3
    assert [t.symbol for t in ref.received] == ["A", "B", "C"]


def test_partial_frame_buffered(proto):
    p, ref = proto
    line = _tick_line("X", 1.0)
    # Send first half, then the rest
    p.dataReceived(line[:len(line) // 2])
    assert len(ref.received) == 0
    p.dataReceived(line[len(line) // 2:])
    assert len(ref.received) == 1


def test_invalid_json_does_not_kill_connection(proto):
    p, ref = proto
    p.dataReceived(b"not even json\n")
    p.dataReceived(_tick_line("X", 1.0))
    # First line dropped silently; second succeeded.
    assert len(ref.received) == 1


def test_unknown_tag_dropped(proto):
    """A JSON object with an unrecognised type discriminator is dropped."""
    p, ref = proto
    p.dataReceived(b'{"type":"who_knows","payload":"junk"}\n')
    p.dataReceived(_tick_line("X", 1.0))
    assert len(ref.received) == 1


def test_oversize_line_drops_connection(proto):
    p, ref = proto
    # Produce a line bigger than max_length=1024 with no terminator
    huge = b"x" * 4096
    p.dataReceived(huge)
    # Expect the transport to be flagged for disconnection.
    assert p.transport.disconnecting


def test_account_decoded(proto):
    p, ref = proto
    a = Account(login="42", currency="USD", balance=100.0, equity=100.0,
                margin=0.0, free_margin=100.0, profit=0.0)
    p.dataReceived(msgspec.json.encode(a) + b"\n")
    assert len(ref.received) == 1
    assert isinstance(ref.received[0], Account)
    assert ref.received[0].login == "42"
