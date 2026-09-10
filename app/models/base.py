"""模型基类与公共类型。

时区约定：全库统一存 **naive UTC**。SQLite 不做时区转换，混用 aware/naive
是这类系统最常见的隐蔽 bug 来源，因此在入口处统一抹掉 tzinfo。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer

from app.database import Base  # noqa: F401  对外统一从 models.base 导入

#: 主键类型。SQLite 下 BIGINT 主键不能作为 rowid 别名，自增会失效，
#: 因此降级为 INTEGER；生产库（PostgreSQL）仍用 BIGINT。
PKType = BigInteger().with_variant(Integer, "sqlite")
FKType = BigInteger().with_variant(Integer, "sqlite")


def utcnow() -> datetime:
    """当前 UTC 时间（naive）。全库统一使用此函数取时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


__all__ = ["Base", "PKType", "FKType", "utcnow", "DateTime"]
