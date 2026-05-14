from __future__ import annotations

from typing import Optional

import msgspec

# ============================================================================
# LoggingConfig
# ============================================================================

class LoggingConfig(msgspec.Struct, frozen=True, gc=False):
    """
    日志栈配置。

    本配置驱动 mt5_bridge.infra.logging.configure_logging() 初始化全局日志栈。
    所有字段均提供合理默认值，零参数构造 LoggingConfig() 即可获得生产可用配置：

        - INFO 级别
        - 控制台彩色输出（自动检测 TTY）
        - 文件输出**默认关闭**（需显式 ``log_to_file=True`` 启用；
          关闭是为防止 import mt5_bridge 子模块即在 CWD 隐式创建 ./logs/）
        - 异步队列容量 100,000（足以吸收数秒磁盘停顿）
        - 服务标识 "mt5_bridge"

    典型用法：
        # 1. 默认配置（仅控制台）
        cfg = LoggingConfig()

        # 2. 启用文件输出（默认 ./logs/mt5_bridge.log）
        cfg = LoggingConfig(log_to_file=True)

        # 3. 调试模式：DEBUG 级别 + 调用点信息
        cfg = LoggingConfig(level="DEBUG", add_callsite=True)

        # 4. JSON 输出（用于日志聚合系统如 ELK）
        cfg = LoggingConfig(json_logs=True)

        # 5. 多服务共享后端：自定义 service 标识
        cfg = LoggingConfig(service="mt5_bridge-prod-east")

    线程安全：
        本类是 frozen msgspec.Struct，构造后完全不可变，可安全跨线程共享。

    序列化：
        本类通过 msgspec 自动支持 JSON / MessagePack 序列化，
        便于从配置文件加载或通过网络传输。

    字段：
        level: 日志级别字符串。
            - 取值："DEBUG" / "INFO" / "WARNING" / "ERROR" / "CRITICAL"
              （大小写不敏感，下游 _coerce_level 统一处理）
            - 默认："INFO"
            - 业务建议：生产 INFO，开发/调试 DEBUG，性能测试 WARNING

        json_logs: 是否使用 JSON 行格式输出。
            - True：每条日志输出为单行 JSON（适合日志聚合系统）
            - False：使用 structlog.dev.ConsoleRenderer 的人类可读格式
            - 默认：False
            - 注意：True 时控制台颜色被强制禁用（避免 ANSI 控制符破坏 JSON）

        add_callsite: 是否附带调用点信息（文件名/行号/函数名）。
            - True：每条日志附加 filename / lineno / func_name 字段
            - False：不附加（节省字符串构造开销）
            - 默认：False
            - 业务建议：仅在调试时启用（每条日志会有可观开销）

        log_to_file: 是否启用文件输出。
            - True：除 stdout 外，同时写入 log_dir/log_file
            - False：仅 stdout 输出（适合容器环境，由编排层收集 stdout）
            - 默认：False（避免 import 即在 CWD 生成 ./logs/，影响测试与 CI）

        log_dir: 日志文件目录（仅 log_to_file=True 时生效）。
            - 默认："./logs"
            - 不存在时自动 makedirs（exist_ok=True）
            - 创建失败时降级为仅 stdout 输出，stderr 提示一次

        log_file: 日志文件名（仅 log_to_file=True 时生效）。
            - 默认："mt5_bridge.log"
            - 完整路径：os.path.join(log_dir, log_file)

        file_max_bytes: 单个日志文件的最大字节数，超过则触发轮转。
            - 默认：100 * 1024 * 1024（100 MB）
            - 业务建议：100 MB ~ 1 GB，过小导致频繁轮转，过大不便查阅
            - 必须 > 0

        file_backup_count: 保留的轮转备份文件数量。
            - 默认：10
            - 总占用磁盘：约 (file_backup_count + 1) * file_max_bytes
            - 必须 >= 0（0 表示禁用备份，仅当前文件，超过即覆盖）

        queue_maxsize: 异步日志队列的容量上限（条数）。
            - 默认：100_000（在每条 ~500 字节估算下约 50 MB 内存）
            - 0 表示无界（不推荐：失去背压保护）
            - 满时业务线程会 BLOCK 而非丢弃记录（保证日志完整性）
            - 必须 >= 0

        service: 顶层服务标识，绑定到 contextvar。
            - 默认："mt5_bridge"
            - None 表示不绑定（适合不需要服务区分的单进程场景）
            - 业务建议：多服务共享日志后端时设置为可识别的标识，
              如 "mt5_bridge-prod-east" / "mt5_bridge-staging" / "mt5_bridge-test"

    校验规则（__post_init__）：
        - queue_maxsize >= 0
        - file_max_bytes > 0
        - file_backup_count >= 0
        校验失败抛 ValueError，错误消息含字段名 + 期望条件 + 实际值。

    不校验的字段：
        - level：字符串校验由 mt5_bridge.infra.logging._coerce_level 统一处理
        - log_dir / log_file：路径合法性由运行时 os.makedirs / open 报错
        - service：任意字符串均合法（包括空字符串）

    对应实现：mt5_bridge.infra.logging.configure_logging
    """
    # ── 日志级别与格式 ──────────────────────────────────────
    level: str = "INFO"
    json_logs: bool = False
    add_callsite: bool = False

    # ── 文件输出 ──────────────────────────────────────────
    # log_to_file 默认 False：避免 import mt5_bridge 子模块即在当前 CWD 隐式生成 ./logs/。
    # 部署/容器场景请显式 LoggingConfig(log_to_file=True, log_dir=...) 启用。
    log_to_file: bool = False
    log_dir: str = "./logs"
    log_file: str = "mt5_bridge.log"
    file_max_bytes: int = 100 * 1024 * 1024  # 100 MB
    file_backup_count: int = 10

    # ── 异步队列 ──────────────────────────────────────────
    queue_maxsize: int = 100_000

    # ── 服务标识 ──────────────────────────────────────────
    service: Optional[str] = "mt5_bridge"

    def __post_init__(self) -> None:
        """
        构造时校验数值字段的合法性。

        校验失败抛 ValueError（业务异常），不使用 assert
        （assert 在 python -O 下会被剥离，配置校验必须始终生效）。

        Raises:
            ValueError: 当任一数值字段不满足约束时
        """
        if self.queue_maxsize < 0:
            raise ValueError(
                f"LoggingConfig.queue_maxsize must be >= 0 "
                f"(0 means unbounded), got {self.queue_maxsize}"
            )

        if self.file_max_bytes <= 0:
            raise ValueError(
                f"LoggingConfig.file_max_bytes must be > 0, "
                f"got {self.file_max_bytes}"
            )

        if self.file_backup_count < 0:
            raise ValueError(
                f"LoggingConfig.file_backup_count must be >= 0, "
                f"got {self.file_backup_count}"
            )


# ============================================================================
# 公开 API
# ============================================================================

__all__ = [
    "LoggingConfig",
]
