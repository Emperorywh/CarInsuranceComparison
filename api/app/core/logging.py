"""安全日志过滤器：任何日志输出前统一脱敏（SPEC §9.7）。

挂在根 logger 的 handler 上；uvicorn access/error 日志由 uvicorn 自行
管理 handler，但本应用从不把令牌或敏感内容放进 URL/查询串，
因此路径级访问日志天然安全。
"""

from __future__ import annotations

import logging

from app.core.privacy import sanitize_text


class SensitiveDataFilter(logging.Filter):
    """对日志消息与插值参数统一脱敏；脱敏自身异常时丢弃该条日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = sanitize_text(str(record.msg))
            if record.args:
                record.args = tuple(
                    sanitize_text(arg) if isinstance(arg, str) else arg for arg in record.args
                )
        except Exception:
            # 隐私优先：脱敏流程本身出错时宁可丢日志也不泄露原文
            return False
        return True


def configure_logging() -> None:
    """应用启动时统一配置根 logger（幂等，可重复调用）。"""
    root = logging.getLogger()
    if any(isinstance(f, SensitiveDataFilter) for f in root.filters):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(SensitiveDataFilter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)
