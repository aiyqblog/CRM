"""终端客户报备（需求文档 4.7）。

模组行业走渠道时最容易出冲突的环节：多个代理商可能同时报备同一个终端客户。
设计原则是**绝不自动覆盖**——撞单必须上报裁决，否则会直接演变成渠道矛盾。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import REPORT_LOCK_STAGE, ProjectStage
from app.models import Customer, CustomerReport, SalesProject
from app.models.base import utcnow
from app.services.errors import Conflict, NotFound, ValidationFailed


def get_active_report(db: Session, end_customer_id: int) -> CustomerReport | None:
    """取当前生效的报备。同一终端客户同一时刻至多一条。"""
    return db.execute(
        select(CustomerReport)
        .where(
            CustomerReport.end_customer_id == end_customer_id,
            CustomerReport.status == CustomerReport.STATUS_ACTIVE,
        )
        .order_by(CustomerReport.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def is_report_effective(report: CustomerReport, on_date: date | None = None) -> bool:
    """报备是否在有效期内。锁定后的报备不因过期失效。"""
    if report.is_locked:
        return True
    today = on_date or utcnow().date()
    return report.start_date <= today <= report.expire_date


def create_report(
    db: Session,
    *,
    user,
    project: SalesProject,
    end_customer: Customer,
    valid_days: int | None = None,
) -> CustomerReport:
    """创建终端客户报备（R-27）。

    冲突时抛 ``Conflict``（HTTP 409），调用方应将其转入主管裁决队列，
    而不是自动覆盖已有报备。
    """
    existing = get_active_report(db, end_customer.id)

    if existing is not None and is_report_effective(existing):
        if existing.project_id == project.id:
            raise Conflict(
                "R-27",
                "该项目已报备该终端客户",
                details={"report_id": existing.id, "expire_date": str(existing.expire_date)},
            )
        raise Conflict(
            "R-27",
            f"终端客户【{end_customer.name}】已被其他项目报备，需主管裁决",
            details={
                "report_id": existing.id,
                "conflict_project_id": existing.project_id,
                "expire_date": str(existing.expire_date),
                "action": "escalate",
            },
        )

    today = utcnow().date()
    days = valid_days or settings.report_valid_days

    report = CustomerReport(
        end_customer_id=end_customer.id,
        project_id=project.id,
        owner_id=user.id,
        start_date=today,
        expire_date=today + timedelta(days=days),
        is_locked=False,
        status=CustomerReport.STATUS_ACTIVE,
        created_by=user.id,
    )
    db.add(report)

    project.end_customer_id = end_customer.id
    db.flush()
    return report


def renew_report(
    db: Session, *, report: CustomerReport, on_date: date | None = None, days: int | None = None
) -> CustomerReport:
    """续期。有效期内有拜访或阶段推进即自动续期（需求文档 4.7）。"""
    if report.status != CustomerReport.STATUS_ACTIVE:
        raise ValidationFailed("RPT-01", "已释放的报备不能续期")

    today = on_date or utcnow().date()
    base = max(today, report.expire_date)
    report.expire_date = base + timedelta(days=days or settings.report_valid_days)
    db.flush()
    return report


def lock_report(db: Session, *, report: CustomerReport, project: SalesProject) -> CustomerReport:
    """项目进入 Design-in 后报备转锁定，不再自动释放。"""
    if _stage_index(project.stage) < _stage_index(REPORT_LOCK_STAGE.value):
        # 未到锁定阶段，保持原状
        return report

    report.is_locked = True
    project.end_customer_locked = True
    db.flush()
    return report


def _stage_index(stage: str) -> int:
    from app.constants import STAGE_ORDER

    return STAGE_ORDER.index(ProjectStage(stage))


def release_expired(db: Session, now: datetime | None = None) -> int:
    """释放已过期且未锁定的报备。由定时任务每日执行。"""
    today = (now or utcnow()).date()
    rows = db.execute(
        select(CustomerReport).where(
            CustomerReport.status == CustomerReport.STATUS_ACTIVE,
            CustomerReport.is_locked.is_(False),
            CustomerReport.expire_date < today,
        )
    ).scalars().all()

    for report in rows:
        report.status = CustomerReport.STATUS_RELEASED

    db.flush()
    return len(rows)


def get_report_or_404(db: Session, report_id: int) -> CustomerReport:
    report = db.get(CustomerReport, report_id)
    if report is None:
        raise NotFound("报备记录不存在")
    return report
