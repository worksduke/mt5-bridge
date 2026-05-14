from __future__ import annotations

import atexit
import logging
import logging.handlers
import queue
import sys
import threading
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Optional, Union

import structlog

from mt5_bridge.configs.logging import LoggingConfig

# ============================================================================
# 模块级全局状态（受 _lock 保护）
# ============================================================================

_lock: threading.RLock = threading.RLock()
"""保护全局状态修改的可重入锁。"""

_configured: bool = False
"""是否已调用 configure_logging。"""

_current_config: Optional[LoggingConfig] = None
"""当前生效的配置（None 表示未 configure）。"""

_queue: Optional[queue.Queue] = None
"""异步日志队列（QueueHandler -> QueueListener 之间的缓冲）。"""

_listener: Optional[logging.handlers.QueueListener] = None
"""后台日志消费线程。"""

_handlers: list[logging.Handler] = []
"""实际 IO 处理器（stdout + 可选 file）。"""

_atexit_registered: bool = False
"""是否已注册 atexit 钩子（仅注册一次）。"""


# ============================================================================
# 上下文变量（线程隔离的日志上下文）
# ============================================================================

_log_context: ContextVar[dict[str, Any]] = ContextVar(
    "cube_log_context", default={}
)
"""
线程隔离的日志上下文。

每个线程（或 contextvars Context）独立持有一份字典，
通过 bind_context() 添加键值对，通过 clear_context() 清空。
所有日志会自动注入这些字段（由 _inject_context_processor 实现）。

典型用法：
    bind_context(cube_id="ABC123", symbol="BTCUSDT")
    logger.info("bar processed")
    # 输出: timestamp=... level=info event=bar processed cube_id=ABC123 symbol=BTCUSDT
"""


# ============================================================================
# 日志级别字符串处理
# ============================================================================

_LEVEL_MAPPING: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
}


def _coerce_level(level: Union[str, int]) -> int:
    """
    将日志级别字符串或整数统一转换为 logging 模块的整数级别。

    Args:
        level: 字符串（"INFO" / "info" / "DEBUG" 等）或整数（10/20/30/40/50）

    Returns:
        logging 模块的标准整数级别

    Raises:
        ValueError: 当 level 不是合法的字符串或整数时
    """
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        upper = level.upper().strip()
        if upper in _LEVEL_MAPPING:
            return _LEVEL_MAPPING[upper]
    raise ValueError(
        f"Invalid log level: {level!r}. "
        f"Expected one of {list(_LEVEL_MAPPING.keys())} or int."
    )


# ============================================================================
# Structlog Processors（自定义处理器）
# ============================================================================

def _inject_context_processor(
    logger: Any, method_name: str, event_dict: dict
) -> dict:
    """
    Structlog processor：将 contextvars 中的字段注入到每条日志。

    在 structlog processor 链中位置：
        - 在 add_log_level 之后
        - 在 TimeStamper 之后
        - 在最终 renderer（ConsoleRenderer/JSONRenderer）之前

    实现：
        从 _log_context.get() 读取当前线程的上下文字典，
        合并到 event_dict 中（不覆盖已有键）。
    """
    ctx = _log_context.get()
    if ctx:
        for key, value in ctx.items():
            if key not in event_dict:
                event_dict[key] = value
    return event_dict


def _ordered_json_processor(
    logger: Any, method_name: str, event_dict: dict
) -> dict:
    """
    Structlog processor：按固定顺序重排字段，便于人眼快速扫描。

    顺序：timestamp / level / event / logger / 其他字段（保持原顺序）

    仅在 json_logs=True 时启用。
    """
    ordered_keys = ("timestamp", "level", "event", "logger")
    ordered: dict = {}
    for key in ordered_keys:
        if key in event_dict:
            ordered[key] = event_dict.pop(key)
    ordered.update(event_dict)
    return ordered


# ============================================================================
# Handler 构造
# ============================================================================

def _build_renderer(json_logs: bool) -> Any:
    """
    构造最终的 structlog renderer（用于 ProcessorFormatter）。

    Args:
        json_logs: True 使用 JSONRenderer，False 使用 ConsoleRenderer

    Returns:
        structlog renderer 实例
    """
    if json_logs:
        return structlog.processors.JSONRenderer()
    # 控制台模式：彩色输出（如果 stdout 是 TTY）
    return structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())


def _build_foreign_pre_chain(add_callsite: bool) -> list:
    """
    构造 ProcessorFormatter 的 foreign_pre_chain。

    foreign_pre_chain 是用于"非 structlog 来源的日志记录"的预处理器链
    （例如第三方库直接用 stdlib logging 输出的日志，也会经过此链）。

    确保所有日志（无论来源）都有一致的字段结构。
    """
    chain: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _inject_context_processor,
    ]
    if add_callsite:
        chain.append(structlog.processors.CallsiteParameterAdder(
            parameters=[
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.LINENO,
                structlog.processors.CallsiteParameter.FUNC_NAME,
            ]
        ))
    return chain


def _build_handlers(
    config: LoggingConfig,
) -> list[logging.Handler]:
    """
    根据配置构造实际的 IO handlers（stdout + 可选 file）。

    Args:
        config: 日志配置

    Returns:
        handlers 列表（至少含 stdout_handler，可能含 file_handler）

    异常处理:
        文件 handler 创建失败时（OSError），降级为仅 stdout 输出，
        并向 stderr 打印一次警告（按预审点 4 决策 B）。

    实现要点（structlog + stdlib logging 桥接）:
        ProcessorFormatter 必须正确处理两类日志:
        - structlog 来源（record.msg 是 dict，已被 wrap_for_formatter 处理）
        - stdlib 来源（record.msg 是 str，第三方库 / threading 内部日志等）

        正确的配置:
        - foreign_pre_chain：将 stdlib 来源的日志补全为 structlog 风格
        - processors（顶层）：合并后的最终渲染链
          必须以 ProcessorFormatter.remove_processors_meta 开头（剥离 wrap_for_formatter 留下的元字段）
        - 错误的旧 API：`processor=renderer`（单数），仅在某些情况下工作，
          会在 stdlib 来源的日志上抛 AttributeError: 'str' object has no attribute 'copy'
    """
    renderer = _build_renderer(config.json_logs)
    foreign_pre_chain = _build_foreign_pre_chain(config.add_callsite)

    # 顶层 processors 链：
    #   1. remove_processors_meta：剥离 wrap_for_formatter 注入的 _record / _from_structlog 元字段
    #   2. （仅 json_logs=True）_ordered_json_processor：强制字段顺序
    #   3. renderer：最终渲染（ConsoleRenderer 或 JSONRenderer）
    top_processors: list = [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
    ]
    if config.json_logs:
        top_processors.append(_ordered_json_processor)
    top_processors.append(renderer)

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=foreign_pre_chain,
        processors=top_processors,
    )

    handlers: list[logging.Handler] = []

    # 1. stdout handler（始终启用）
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    handlers.append(stdout_handler)

    # 2. file handler（按配置启用，失败时降级）
    if config.log_to_file:
        try:
            log_dir = Path(config.log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / config.log_file

            file_handler = logging.handlers.RotatingFileHandler(
                filename=str(log_path),
                maxBytes=config.file_max_bytes,
                backupCount=config.file_backup_count,
                encoding="utf-8",
                delay=False,
            )
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
        except OSError as e:
            # 降级：仅 stdout，并打印警告到 stderr（一次性）
            print(
                f"[cube.utils.logging] WARNING: failed to create log file "
                f"at {config.log_dir}/{config.log_file}: {e}. "
                f"Falling back to stdout-only logging.",
                file=sys.stderr,
                flush=True,
            )

    return handlers


# ============================================================================
# Structlog 全局配置
# ============================================================================

def _configure_structlog(config: LoggingConfig) -> None:
    """
    配置 structlog 全局 processor 链。

    structlog 来源的日志的 processor 链：
        1. contextvars.merge_contextvars  - 合并 structlog 自身的 contextvars
        2. add_log_level                   - 添加 level 字段
        3. add_logger_name                 - 添加 logger 字段
        4. TimeStamper                     - 添加 timestamp 字段（ISO UTC）
        5. _inject_context_processor       - 注入 cube 自定义 contextvars
        6. CallsiteParameterAdder（可选）   - 添加 filename/lineno/func_name
        7. ProcessorFormatter.wrap_for_formatter  - 转交给 stdlib logging

    注意：
        - 顶层处理器（不含 _ordered_json_processor 与 renderer）
          这两个由 ProcessorFormatter 在最终渲染时统一处理（见 _build_handlers）
        - 这种"分层"设计的好处：stdlib 来源的日志经过 foreign_pre_chain 后，
          也会经过相同的 _ordered_json_processor + renderer，保证输出一致

    Args:
        config: 日志配置
    """
    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _inject_context_processor,
    ]

    if config.add_callsite:
        processors.append(structlog.processors.CallsiteParameterAdder(
            parameters=[
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.LINENO,
                structlog.processors.CallsiteParameter.FUNC_NAME,
            ]
        ))

    # 最后一步：转交给 stdlib logging（由 ProcessorFormatter 完成最终渲染）
    # 注意：_ordered_json_processor 与 renderer 不在此链中,
    # 而是由 _build_handlers 中的 ProcessorFormatter.processors 统一处理
    processors.append(structlog.stdlib.ProcessorFormatter.wrap_for_formatter)

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

# ============================================================================
# 自定义 QueueHandler（修复与 structlog 的兼容问题）
# ============================================================================

class _NonMutatingQueueHandler(logging.handlers.QueueHandler):
    """
    自定义 QueueHandler：不修改 LogRecord 的 msg / args 字段。

    背景：
        stdlib 的 logging.handlers.QueueHandler.prepare() 默认实现会:
            1. 调用 self.format(record)，将 record.msg 转换为格式化后的字符串
            2. 将 record.msg 替换为该字符串
            3. 将 record.args 设置为 None

        这种行为是为"跨进程传递 LogRecord"设计的（multiprocessing.Queue
        需要 pickle，而 dict 形式的 msg 可能无法 pickle）。

        但对于"同进程内通过 queue.Queue 传递"的场景（本系统使用方式），
        这种字符串化反而是有害的：

        - structlog 经 wrap_for_formatter 输出的 record.msg 是 dict
        - 同时 record._from_structlog 属性被设置为 True
        - QueueHandler.prepare() 将 dict 字符串化，但保留 _from_structlog 标记
        - 下游 ProcessorFormatter 看到 _from_structlog=True 走 structlog 路径,
          尝试对 record.msg 调用 .copy()，但 record.msg 此时已是 str
        - 抛出 AttributeError: 'str' object has no attribute 'copy'

    修复方案:
        重写 prepare()，直接返回原始 record，不做任何修改。
        record 在同进程内通过 queue.Queue 传递，无需任何序列化处理。

    适用范围:
        本系统的所有 logging 路径（同进程多线程，无 multiprocessing）。

    参考:
        - structlog issue #459 (官方推荐的 QueueHandler 集成方式)
        - Python 官方文档对 QueueHandler.prepare() 的说明
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        """
        直接返回原 record，不修改任何字段。

        与 stdlib 默认实现的差异：
            - 不调用 self.format(record)
            - 不修改 record.msg
            - 不修改 record.args
            - 保留 record._from_structlog 等所有自定义属性

        Args:
            record: stdlib LogRecord 对象

        Returns:
            原 record 对象（未修改）
        """
        return record



# ============================================================================
# 公开 API：configure_logging
# ============================================================================



def configure_logging(config: Optional[LoggingConfig] = None) -> None:
    """
    初始化全局日志栈（structlog + stdlib logging + 异步队列 + 文件轮转）。

    幂等性：
        - 重复调用且 config 与上次相同：no-op（直接返回）
        - 重复调用但 config 不同：先调用 shutdown_logging() 再重新初始化，
          并打印 warning 到 stderr 提示用户

    Args:
        config: 日志配置。None 时使用 LoggingConfig() 的默认配置。

    线程安全：
        通过 _lock 保护，可跨线程调用。

    副作用：
        - 修改 stdlib logging 的 root logger 配置
        - 修改 structlog 全局 processor 链
        - 启动后台 QueueListener 线程
        - 注册 atexit 钩子（首次调用时）
        - 如果启用文件输出，会在 log_dir 创建/打开日志文件

    示例：
        # 默认配置
        configure_logging()

        # 自定义配置
        configure_logging(LoggingConfig(level="DEBUG", json_logs=True))

        # service 标识（多服务共享后端时区分来源）
        configure_logging(LoggingConfig(service="cube-prod"))
    """
    global _configured, _current_config, _queue, _listener, _handlers
    global _atexit_registered

    if config is None:
        config = LoggingConfig()

    with _lock:
        # 幂等检查
        if _configured:
            if _current_config == config:
                return  # 完全相同，no-op
            # 配置不同：重新初始化
            print(
                "[cube.utils.logging] WARNING: configure_logging() called "
                "with different config; reinitializing logging stack.",
                file=sys.stderr,
                flush=True,
            )
            _shutdown_logging_locked(timeout_seconds=5.0)

        # 1. 配置 structlog 全局 processor 链
        _configure_structlog(config)

        # 2. 构造实际 IO handlers
        handlers = _build_handlers(config)
        for h in handlers:
            h.setLevel(_coerce_level(config.level))
        _handlers = handlers

        # 3. 创建异步队列
        # queue_maxsize=0 表示无界（虽然 LoggingConfig 不推荐，但允许）
        q: queue.Queue = queue.Queue(maxsize=config.queue_maxsize)
        _queue = q

        # 4. 创建并启动 QueueListener
        listener = logging.handlers.QueueListener(
            q, *handlers, respect_handler_level=True
        )
        listener.start()
        _listener = listener

        # 5. 配置 root logger：清除旧 handlers，添加 QueueHandler
        root = logging.getLogger()
        root.setLevel(_coerce_level(config.level))
        # 清除所有现有 handlers（避免重复输出）
        for h in list(root.handlers):
            root.removeHandler(h)
        root.addHandler(_NonMutatingQueueHandler(q))

        # 6. 绑定 service 上下文（如配置了）
        if config.service is not None:
            structlog.contextvars.bind_contextvars(service=config.service)

        # 7. 注册 atexit 钩子（仅首次）
        if not _atexit_registered:
            atexit.register(_atexit_shutdown)
            _atexit_registered = True

        # 8. 标记已配置
        _configured = True
        _current_config = config


# ============================================================================
# 公开 API：shutdown_logging
# ============================================================================

def _shutdown_logging_locked(timeout_seconds: float = 5.0) -> None:
    """
    内部实现：在已持有 _lock 的情况下执行 shutdown。

    分离出来是为了 configure_logging 在"配置变更需重新初始化"时可以
    在已持锁的情况下安全调用，避免 RLock 的递归计数过多。
    """
    global _configured, _current_config, _queue, _listener, _handlers

    if not _configured:
        return  # 未配置，no-op

    # 1. 停止 QueueListener（会处理完队列中的剩余消息）
    if _listener is not None:
        try:
            _listener.stop()  # 阻塞直到队列处理完毕
        except Exception as e:
            print(
                f"[cube.utils.logging] WARNING: error stopping QueueListener: {e}",
                file=sys.stderr,
                flush=True,
            )
        _listener = None

    # 2. 关闭所有 handler
    for h in _handlers:
        try:
            h.close()
        except Exception as e:
            print(
                f"[cube.utils.logging] WARNING: error closing handler {h!r}: {e}",
                file=sys.stderr,
                flush=True,
            )
    _handlers = []

    # 3. 清理 root logger 上的 QueueHandler
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, logging.handlers.QueueHandler):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    # 4. 清空全局状态
    _queue = None
    _configured = False
    _current_config = None


def shutdown_logging(timeout_seconds: float = 5.0) -> None:
    """
    优雅关闭日志栈：刷新队列 + 停止 listener + 关闭所有 handler。

    幂等：未 configure 或已 shutdown 时调用是 no-op。

    Args:
        timeout_seconds: 等待队列处理完毕的最大时间（当前实现下，
            QueueListener.stop() 会阻塞至队列空，timeout_seconds 暂未使用，
            保留参数以兼容未来 API 演进）

    线程安全：
        通过 _lock 保护，可跨线程调用。

    典型用法：
        # 在程序退出前显式调用（atexit 会自动调用，但显式调用更可控）
        shutdown_logging()
    """
    with _lock:
        _shutdown_logging_locked(timeout_seconds)


def _atexit_shutdown() -> None:
    """
    atexit 钩子：进程退出时自动调用 shutdown_logging。

    使用 try/except 防止退出时的异常传播（atexit 异常会被 Python 打印到 stderr，
    但不影响退出码）。
    """
    try:
        shutdown_logging()
    except Exception as e:
        print(
            f"[cube.utils.logging] WARNING: error in atexit shutdown: {e}",
            file=sys.stderr,
            flush=True,
        )


# ============================================================================
# 公开 API：get_logger
# ============================================================================

def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    获取一个 structlog BoundLogger 实例。

    懒初始化：
        如果尚未调用 configure_logging，会自动调用 configure_logging()
        使用默认配置（按预审点 1 决策 B）。这让"零配置即可使用"成为可能：

            logger = get_logger("my_module")  # 自动 configure
            logger.info("hello")

    Args:
        name: logger 名称（通常使用 __name__）

    Returns:
        structlog.stdlib.BoundLogger 实例

    线程安全：
        get_logger 自身是线程安全的（structlog 内部线程安全）。
        懒初始化时通过 _lock 保护，避免多线程同时初始化。

    典型用法：
        # 模块级 logger
        logger = get_logger(__name__)

        # 使用结构化字段
        logger.info("bar processed", bar_id=123, symbol="BTCUSDT")

        # 异常日志
        try:
            ...
        except Exception:
            logger.exception("failed to process", bar_id=123)
    """
    # 懒初始化（按预审点 1 决策 B）
    if not _configured:
        with _lock:
            if not _configured:  # 双重检查锁
                configure_logging()

    return structlog.get_logger(name)


# ============================================================================
# 公开 API：上下文绑定
# ============================================================================

def bind_context(**kwargs: Any) -> None:
    """
    绑定上下文变量到当前线程的 logging context。

    所有后续日志会自动包含这些字段（通过 _inject_context_processor 注入）。

    线程隔离：
        基于 contextvars，每个线程（或 contextvars Context）独立持有上下文。
        SessionActor 在不同线程中运行，各自的 bind_context 互不影响。

    叠加而非覆盖：
        新调用的 bind_context 会与现有上下文合并，相同 key 会被新值覆盖。

    Args:
        **kwargs: 要绑定的键值对（如 cube_id="ABC", symbol="BTCUSDT"）

    典型用法：
        # SessionActor 启动时绑定 symbol
        bind_context(symbol="BTCUSDT")

        # Cube 创建时追加 cube_id
        bind_context(cube_id="ABC123")

        # 之后所有日志都会自动包含 symbol 和 cube_id
        logger.info("bar processed", bar_id=42)
        # 输出: ... event="bar processed" bar_id=42 symbol=BTCUSDT cube_id=ABC123
    """
    current = _log_context.get()
    new = {**current, **kwargs}
    _log_context.set(new)


def unbind_context(*keys: str) -> None:
    """
    从当前线程的 logging context 中移除指定的键。

    Args:
        *keys: 要移除的键名（不存在的键会被忽略）

    典型用法：
        bind_context(cube_id="ABC123")
        # ... 处理 ...
        unbind_context("cube_id")  # Cube 销毁后移除
    """
    current = _log_context.get()
    if not current:
        return
    new = {k: v for k, v in current.items() if k not in keys}
    _log_context.set(new)


def clear_context() -> None:
    """
    清空当前线程的 logging context。

    典型用法：
        # 在测试 fixture 的 teardown 中清理
        clear_context()
    """
    _log_context.set({})


def get_context() -> dict[str, Any]:
    """
    获取当前线程的 logging context（只读副本）。

    Returns:
        当前 context 的字典副本（修改副本不会影响 context）

    典型用法（调试）：
        ctx = get_context()
        print(f"Current logging context: {ctx}")
    """
    return dict(_log_context.get())


# ============================================================================
# 公开 API
# ============================================================================

__all__ = [
    # 配置生命周期
    "configure_logging",
    "shutdown_logging",
    # Logger 获取
    "get_logger",
    # 上下文管理
    "bind_context",
    "unbind_context",
    "clear_context",
    "get_context",
]
