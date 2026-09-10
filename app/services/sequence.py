"""编号生成。

规则见需求文档：``VST/VSR/PRJ + yyyyMMdd + 4位流水``。

注意：``next_code`` 采用「查当日最大值 +1」，在并发下存在竞争窗口。
生产环境应改为数据库序列或加行锁；单机 + SQLite 场景下用唯一索引兜底即可
（重复会被数据库拒绝，重试一次即可拿到新号）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.base import utcnow

VISIT_PLAN_PREFIX = "VST"
VISIT_RECORD_PREFIX = "VSR"
PROJECT_PREFIX = "PRJ"

_SEQ_WIDTH = 4


def next_code(
    db: Session,
    model: type,
    field_name: str,
    prefix: str,
    when: datetime | None = None,
) -> str:
    """生成 ``前缀 + 日期 + 流水`` 形式的编号。"""
    day = (when or utcnow()).strftime("%Y%m%d")
    pattern = f"{prefix}{day}%"
    column = getattr(model, field_name)

    last = db.execute(
        select(column).where(column.like(pattern)).order_by(column.desc()).limit(1)
    ).scalar_one_or_none()

    seq = 1
    if last:
        tail = str(last)[-_SEQ_WIDTH:]
        if tail.isdigit():
            seq = int(tail) + 1

    return f"{prefix}{day}{seq:0{_SEQ_WIDTH}d}"


def next_visit_plan_no(db: Session, when: datetime | None = None) -> str:
    from app.models import VisitPlan

    return next_code(db, VisitPlan, "plan_no", VISIT_PLAN_PREFIX, when)


def next_visit_record_no(db: Session, when: datetime | None = None) -> str:
    from app.models import VisitRecord

    return next_code(db, VisitRecord, "record_no", VISIT_RECORD_PREFIX, when)


def next_project_no(db: Session, when: datetime | None = None) -> str:
    from app.models import SalesProject

    return next_code(db, SalesProject, "project_no", PROJECT_PREFIX, when)
