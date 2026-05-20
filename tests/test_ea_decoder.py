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
    Cube,
    HistoryCube,
    HistoryCubeDone,
    HistoryMetaCube,
    HistoryMetaCubeDone,
    MetaCube,
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


# =============================================================================
# Cube / MetaCube wire types (v0.3)
# =============================================================================

_CUBE_FIELDS = (
    b'"symbol":"XAUUSD","role":"execution","tf_period":"M5","id":7,'
    b'"dir":"UP","state":"DEAD","bar_count":4,'
    b'"body_high":2050.5,"body_low":2049.0,"wick_high":2051.0,"wick_low":2048.5,'
    b'"first_open":2049.5,"last_close":2050.2,'
    b'"bull_volume":150,"bear_volume":50,"obv_score":0.5,"efficiency":0.85,'
    b'"total_volume":200,"time_start":"2026.05.20 09:00:00",'
    b'"time_end":"2026.05.20 09:20:00"'
)


def test_decode_cube_closed():
    line = b'{"type":"cube",' + _CUBE_FIELDS + b',"is_closed":true}'
    msg = DECODER.decode(line)
    assert isinstance(msg, Cube)
    assert msg.symbol == "XAUUSD"
    assert msg.id == 7
    assert msg.dir == "UP"
    assert msg.state == "DEAD"
    assert msg.is_closed is True
    assert msg.bar_count == 4
    assert msg.obv_score == 0.5


def test_decode_cube_forming():
    """Forming cubes are pushed every tick interval with is_closed=false."""
    line = (b'{"type":"cube","symbol":"XAUUSD","role":"execution","tf_period":"M5",'
            b'"id":0,"dir":"NONE","state":"FORMING","bar_count":1,'
            b'"body_high":2050.5,"body_low":2049.5,"wick_high":2051.0,"wick_low":2049.0,'
            b'"first_open":2050.0,"last_close":2050.2,'
            b'"bull_volume":10,"bear_volume":0,"obv_score":1.0,"efficiency":0.2,'
            b'"total_volume":10,"time_start":"2026.05.20 09:00:00",'
            b'"time_end":"2026.05.20 09:00:00","is_closed":false}')
    msg = DECODER.decode(line)
    assert isinstance(msg, Cube)
    assert msg.is_closed is False
    assert msg.state == "FORMING"
    assert msg.dir == "NONE"


def test_decode_history_cube():
    """history_cube has same payload shape as cube; is_closed always true."""
    line = b'{"type":"history_cube",' + _CUBE_FIELDS + b',"is_closed":true}'
    msg = DECODER.decode(line)
    assert isinstance(msg, HistoryCube)
    assert msg.id == 7
    assert msg.is_closed is True


def test_decode_history_cube_done():
    line = (b'{"type":"history_cube_done","symbol":"XAUUSD",'
            b'"role":"execution","tf_period":"M5","count":42}')
    msg = DECODER.decode(line)
    assert isinstance(msg, HistoryCubeDone)
    assert msg.count == 42
    assert msg.role == "execution"
    assert msg.tf_period == "M5"


_META_FIELDS = (
    b'"symbol":"XAUUSD","role":"tactical","tf_period":"H1","id":3,'
    b'"dir":"DOWN","state":"DEAD","cube_count":5,"bar_count":18,'
    b'"body_high":2055.0,"body_low":2040.0,"wick_high":2057.5,"wick_low":2038.5,'
    b'"first_open":2054.0,"last_close":2041.0,'
    b'"bull_volume":300,"bear_volume":900,"obv_score":-0.5,"efficiency":0.65,'
    b'"total_volume":1200,"first_cube_id":11,"last_cube_id":15,'
    b'"time_start":"2026.05.20 03:00:00","time_end":"2026.05.20 08:00:00"'
)


def test_decode_meta_cube():
    line = b'{"type":"meta_cube",' + _META_FIELDS + b',"is_closed":true}'
    msg = DECODER.decode(line)
    assert isinstance(msg, MetaCube)
    assert msg.cube_count == 5
    assert msg.first_cube_id == 11
    assert msg.last_cube_id == 15
    assert msg.dir == "DOWN"
    assert msg.obv_score == -0.5


def test_decode_history_meta_cube():
    line = b'{"type":"history_meta_cube",' + _META_FIELDS + b',"is_closed":true}'
    msg = DECODER.decode(line)
    assert isinstance(msg, HistoryMetaCube)
    assert msg.is_closed is True
    assert msg.last_cube_id == 15


def test_decode_history_meta_cube_done():
    line = (b'{"type":"history_meta_cube_done","symbol":"XAUUSD",'
            b'"role":"tactical","tf_period":"H1","count":7}')
    msg = DECODER.decode(line)
    assert isinstance(msg, HistoryMetaCubeDone)
    assert msg.count == 7


def test_decode_unknown_type_raises():
    line = b'{"type":"who_knows","payload":"junk"}'
    try:
        DECODER.decode(line)
    except msgspec.ValidationError:
        return
    raise AssertionError("expected ValidationError for unknown tag")
