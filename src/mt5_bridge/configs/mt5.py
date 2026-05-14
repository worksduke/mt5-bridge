from __future__ import annotations

import tomllib
from pathlib import Path

import msgspec


class MT5Config(msgspec.Struct, frozen=True, gc=False):

    # MT5 配置
    mt5_account:    int  = 0
    mt5_password:   str  = ""
    mt5_server:     str  = ""
    # None = 让 MT5 SDK 自己探测已安装的终端；
    # 否则必须是 terminal64.exe 所在目录的绝对路径。
    mt5_local_path: Path | None = None



def load_mt5_config(path: str | Path) -> MT5Config:
    """Load and validate a TOML config file.

    Args:
        path: Path to a TOML file with the schema documented in
            ``config.example.toml``.

    Returns:
        Frozen ``MT5Config``.

    Raises:
        FileNotFoundError, tomllib.TOMLDecodeError, ValueError
    """
    p = Path(path)
    with p.open("rb") as f:
        data = tomllib.load(f)

    mt5 = data.get("mt5", {})

    raw_path = (mt5.get("mt5_local_path") or "").strip()

    return MT5Config(
        mt5_account    = mt5.get("account", 0),
        mt5_password   = mt5.get("password", ""),
        mt5_server     = mt5.get("server", ""),
        # Path("") evaluates to ``WindowsPath('.')`` whose ``str()`` is
        # ``"."`` — and ``"."`` is truthy, so the old "if path:" check
        # would forward ``path="."`` to MT5 and cause IPC init failure.
        # Keep the field None when the user didn't supply a path.
        mt5_local_path = Path(raw_path) if raw_path else None,
    )


__all__ = ["MT5Config", "load_mt5_config"]
