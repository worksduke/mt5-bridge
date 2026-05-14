"""DECODER round-trip tests.

The dispatcher trusts ``DECODER`` to map each ``"type"`` discriminator
to the right msgspec class. Lock that mapping here so adding a new
wire type can't accidentally shift an existing one.
"""
from __future__ import annotations

import msgspec

from mt5_bridge.contracts.ea_messages import (
    DECODER,
    Account,
    Connected,
    Order,
    OrderRemoved,
    Position,
    PositionClosed,
    Tick,
)


def test_decode_tick():
    line = (b'{"type":"tick","symbol":"XAUUSD","time":"2026.05.14 00:00:00",'
            b'"time_msc":1747180800000,"bid":2050.5,"ask":2051.0,"last":0.0,"volume":1}')
    msg = DECODER.decode(line)
    assert isinstance(msg, Tick)
    assert msg.symbol == "XAUUSD"
    assert msg.bid == 2050.5


def test_decode_connected():
    line = b'{"type":"connected","symbol":"XAUUSD","time":1747180800}'
    msg = DECODER.decode(line)
    assert isinstance(msg, Connected)
    assert msg.symbol == "XAUUSD"


def test_decode_account():
    line = (b'{"type":"account","login":"42","currency":"USD","balance":1000.0,'
            b'"equity":1010.0,"margin":50.0,"free_margin":960.0,"profit":10.0}')
    msg = DECODER.decode(line)
    assert isinstance(msg, Account)
    assert msg.login == "42"
    assert msg.equity == 1010.0


def test_decode_position_with_magic_and_comment():
    """Current EA sends magic + comment; decoder must capture them."""
    line = (b'{"type":"position","ticket":12345,"symbol":"XAUUSD","pos_type":"BUY",'
            b'"volume":0.5,"open_price":2050.0,"cur_price":2051.0,"sl":2040.0,'
            b'"tp":2060.0,"profit":15.0,"magic":1234,"comment":"my trade"}')
    msg = DECODER.decode(line)
    assert isinstance(msg, Position)
    assert msg.ticket == 12345
    assert msg.magic == 1234
    assert msg.comment == "my trade"


def test_decode_position_legacy_without_magic():
    """Older EA recordings may omit magic/comment — defaults must apply."""
    line = (b'{"type":"position","ticket":1,"symbol":"X","pos_type":"BUY",'
            b'"volume":0.1,"open_price":1.0,"cur_price":1.0,'
            b'"sl":0.0,"tp":0.0,"profit":0.0}')
    msg = DECODER.decode(line)
    assert isinstance(msg, Position)
    assert msg.magic == 0
    assert msg.comment == ""


def test_decode_order():
    line = (b'{"type":"order","ticket":99,"symbol":"X","order_type":"BUY_LIMIT",'
            b'"volume":0.1,"open_price":1.0,"sl":0.0,"tp":0.0,"comment":"x","magic":7}')
    msg = DECODER.decode(line)
    assert isinstance(msg, Order)
    assert msg.magic == 7


def test_decode_position_closed():
    line = (b'{"type":"position_closed","ticket":12345,"symbol":"XAUUSD",'
            b'"pos_type":"BUY","volume":0.5,"open_price":2050.0,'
            b'"close_price":2055.0,"profit":250.0,"magic":1234,'
            b'"close_time":1747180900}')
    msg = DECODER.decode(line)
    assert isinstance(msg, PositionClosed)
    assert msg.ticket == 12345
    assert msg.close_price == 2055.0
    assert msg.profit == 250.0
    assert msg.close_time == 1747180900


def test_decode_order_removed():
    line = (b'{"type":"order_removed","ticket":99,"symbol":"X",'
            b'"order_type":"BUY_LIMIT","volume":0.1,"open_price":1.0,'
            b'"sl":0.0,"tp":0.0,"magic":7,"reason":"canceled",'
            b'"removed_time":1747180900}')
    msg = DECODER.decode(line)
    assert isinstance(msg, OrderRemoved)
    assert msg.reason == "canceled"
    assert msg.removed_time == 1747180900


def test_decode_unknown_type_raises():
    line = b'{"type":"who_knows","payload":"junk"}'
    try:
        DECODER.decode(line)
    except msgspec.ValidationError:
        return
    raise AssertionError("expected ValidationError for unknown tag")
