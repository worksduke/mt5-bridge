"""Bridge config: RPC + Retry + Watchdog + Kelly subsections.

Loaded from the same TOML file as ``MT5Config`` via
``load_bridge_config(path)``. All sections are optional in the TOML
— missing sections fall back to ``enabled=False`` defaults so the
bridge can boot in a "minimal" mode for tests.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import msgspec


# =============================================================================
# RPC
# =============================================================================
class RpcEndpoint(msgspec.Struct, frozen=True, gc=False):
    """One TCP listener for one EA bridge."""

    symbol:     str
    host:       str = "127.0.0.1"
    port:       int = 5500
    delimiter:  bytes = b"\n"
    max_length: int = 1 * 1024 * 1024


class RpcConfig(msgspec.Struct, frozen=True, gc=False):
    """RPC subsystem configuration.

    When ``enabled`` is False, twisted is NOT imported anywhere in the
    bridge — users must call ``MT5Bridge.feed_input(msg)`` manually
    with parsed ``EAMessage`` instances.
    """

    enabled:   bool = False
    endpoints: tuple[RpcEndpoint, ...] = ()


# =============================================================================
# Retry
# =============================================================================
class RetryConfig(msgspec.Struct, frozen=True, gc=False):
    """Retry policy for failed trade commands.

    ``retryable_retcodes`` is a strict whitelist — any retcode NOT in
    this set causes immediate failure (no retry). Defaults cover only
    genuinely transient broker / network failures.

    NEVER include hard-fail codes like INVALID_STOPS, NO_MONEY,
    TRADE_DISABLED, CLIENT_DISABLES_AT (10027) or SERVER_DISABLES_AT
    (10026) — those reflect persistent state that retrying cannot fix
    and just spams orders.

    Defaults:
        10004 REQUOTE           — broker requoted
        10006 REJECT            — request rejected (often retryable)
        10012 TIMEOUT           — broker timeout
        10020 PRICE_CHANGED     — price moved before fill
        10021 PRICE_OFF         — no quotes for processing
        10024 TOO_MANY_REQUESTS — rate limit hit
        10031 CONNECTION        — broker connection issue
    """

    enabled:            bool = False
    max_attempts:       int = 3
    base_delay_ms:      int = 100
    max_delay_ms:       int = 2000
    jitter_ratio:       float = 0.2
    retryable_retcodes: tuple[int, ...] = (10004, 10006, 10012, 10020, 10021, 10024, 10031)


# =============================================================================
# Watchdog
# =============================================================================
class WatchdogConfig(msgspec.Struct, frozen=True, gc=False):
    """Tick freshness monitor configuration.

    When ``symbols`` is empty, the watchdog tracks every symbol it
    sees a Tick for (lazy registration). Otherwise it only tracks
    the named ones.
    """

    enabled:                bool = False
    tick_timeout_seconds:   float = 300.0
    check_interval_seconds: float = 5.0
    symbols:                tuple[str, ...] = ()


# =============================================================================
# Kelly
# =============================================================================
class KellyConfig(msgspec.Struct, frozen=True, gc=False):
    """Kelly position-sizing actor configuration.

    ``default_fraction`` is a multiplier applied to the raw Kelly
    fraction when the caller doesn't pass an explicit override.
    Half-Kelly (0.5) is industry-standard for production sizing.
    ``min_volume`` / ``max_volume`` clamp the result; ``volume_step``
    rounds DOWN to the broker-tradable lot increment.
    """

    enabled:          bool = False
    default_fraction: float = 0.5
    min_volume:       float = 0.01
    max_volume:       float = 10.0
    volume_step:      float = 0.01


# =============================================================================
# Aggregate
# =============================================================================
class BridgeConfig(msgspec.Struct, frozen=True, gc=False):
    rpc:      RpcConfig
    retry:    RetryConfig
    watchdog: WatchdogConfig
    kelly:    KellyConfig


def load_bridge_config(path: str | Path) -> BridgeConfig:
    """Load ``[rpc]`` / ``[retry]`` / ``[watchdog]`` / ``[kelly]`` from TOML.

    Missing sections fall back to ``enabled=False`` defaults so a
    minimal config (only ``[mt5]`` filled in) still boots.
    """
    p = Path(path)
    with p.open("rb") as f:
        data = tomllib.load(f)

    rpc_raw = data.get("rpc", {})
    endpoints = tuple(
        RpcEndpoint(
            symbol     = ep["symbol"],
            host       = ep.get("host", "127.0.0.1"),
            port       = int(ep.get("port", 5500)),
            delimiter  = ep.get("delimiter", "\n").encode("utf-8"),
            max_length = int(ep.get("max_length", 1 * 1024 * 1024)),
        )
        for ep in rpc_raw.get("endpoints", [])
    )
    rpc = RpcConfig(enabled=bool(rpc_raw.get("enabled", False)), endpoints=endpoints)

    retry_raw = data.get("retry", {})
    retry = RetryConfig(
        enabled            = bool(retry_raw.get("enabled", False)),
        max_attempts       = int(retry_raw.get("max_attempts", 3)),
        base_delay_ms      = int(retry_raw.get("base_delay_ms", 100)),
        max_delay_ms       = int(retry_raw.get("max_delay_ms", 2000)),
        jitter_ratio       = float(retry_raw.get("jitter_ratio", 0.2)),
        retryable_retcodes = tuple(int(c) for c in retry_raw.get(
            "retryable_retcodes",
            (10004, 10006, 10012, 10020, 10021, 10024, 10031))),
    )

    wd_raw = data.get("watchdog", {})
    watchdog = WatchdogConfig(
        enabled                = bool(wd_raw.get("enabled", False)),
        tick_timeout_seconds   = float(wd_raw.get("tick_timeout_seconds", 300.0)),
        check_interval_seconds = float(wd_raw.get("check_interval_seconds", 5.0)),
        symbols                = tuple(wd_raw.get("symbols", ())),
    )

    k_raw = data.get("kelly", {})
    kelly = KellyConfig(
        enabled          = bool(k_raw.get("enabled", False)),
        default_fraction = float(k_raw.get("default_fraction", 0.5)),
        min_volume       = float(k_raw.get("min_volume", 0.01)),
        max_volume       = float(k_raw.get("max_volume", 10.0)),
        volume_step      = float(k_raw.get("volume_step", 0.01)),
    )

    return BridgeConfig(rpc=rpc, retry=retry, watchdog=watchdog, kelly=kelly)


__all__ = [
    "BridgeConfig",
    "KellyConfig",
    "RetryConfig",
    "RpcConfig",
    "RpcEndpoint",
    "WatchdogConfig",
    "load_bridge_config",
]
