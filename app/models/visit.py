"""拜访计划、拜访记录与附件。

对应需求文档 3.3 节的三张表。校验规则（R-01~R-13）在
``app.services.visit`` 中实现，本层只负责结构。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants import (
    LocationStatus,
    PlanStatus,
    RecordStatus,
    SyncStatus,
    VisitType,
)
from app.models.base import Base, FKType, PKType, utcnow


class VisitPlan(Base):
    """拜访计划（需求文档 3.3 表 1）。"""

    __tablename__ = "visit_plan"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    plan_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    owner_id: Mapped[int] = mapped_column(FKType, ForeignKey("crm_user.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(
        FKType, ForeignKey("customer.id"), nullable=False, index=True
    )
    contact_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    project_id: Mapped[int | None] = mapped_column(
        FKType, ForeignKey("sales_project.id"), nullable=True
    )

    plan_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    plan_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    visit_type: Mapped[int] = mapped_column(Integer, nullable=False, default=VisitType.ROUTINE)
    visit_purpose: Mapped[str] = mapped_column(String(500), nullable=False)

    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    longitude: Mapped[float | None] = mapped_column(Numeric(10, 7), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Numeric(10, 7), nullable=True)

    remind_before: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=PlanStatus.PENDING)
    cancel_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_by: Mapped[int | None] = mapped_column(FKType, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    updated_by: Mapped[int | None] = mapped_column(FKType, nullable=True)

    customer: Mapped["object"] = relationship("Customer", foreign_keys=[customer_id])
    owner: Mapped["object"] = relationship("User", foreign_keys=[owner_id])

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VisitPlan {self.plan_no} status={self.status}>"


class VisitRecord(Base):
    """拜访记录（需求文档 3.3 表 2）。

    ``abnormal_flag`` 由异常判定统一写入（时长过短、定位异常、时间漂移）。
    """

    __tablename__ = "visit_record"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    record_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    plan_id: Mapped[int | None] = mapped_column(FKType, ForeignKey("visit_plan.id"), nullable=True)
    customer_id: Mapped[int] = mapped_column(
        FKType, ForeignKey("customer.id"), nullable=False, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        FKType, ForeignKey("sales_project.id"), nullable=True, index=True
    )
    owner_id: Mapped[int] = mapped_column(FKType, ForeignKey("crm_user.id"), nullable=False)
    #: 拜访类型冗余在记录上：临时拜访没有 plan，若只存在 plan 上就无法按类型筛选
    visit_type: Mapped[int] = mapped_column(
        Integer, nullable=False, default=VisitType.ROUTINE
    )

    checkin_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    checkin_lng: Mapped[float] = mapped_column(Numeric(10, 7), nullable=False)
    checkin_lat: Mapped[float] = mapped_column(Numeric(10, 7), nullable=False)
    checkin_address: Mapped[str] = mapped_column(String(255), nullable=False)
    checkout_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)

    location_status: Mapped[int] = mapped_column(
        Integer, nullable=False, default=LocationStatus.NORMAL
    )
    distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: R-03：超出围栏时强制填写的说明
    location_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    customer_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    next_visit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    project_stage_after: Mapped[str | None] = mapped_column(String(32), nullable=True)

    status: Mapped[int] = mapped_column(Integer, nullable=False, default=RecordStatus.ONGOING)
    abnormal_flag: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    abnormal_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sync_status: Mapped[int] = mapped_column(Integer, nullable=False, default=SyncStatus.SYNCED)
    is_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_by: Mapped[int | None] = mapped_column(FKType, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    updated_by: Mapped[int | None] = mapped_column(FKType, nullable=True)

    attachments: Mapped[list["VisitAttachment"]] = relationship(
        "VisitAttachment", back_populates="record", cascade="all, delete-orphan"
    )
    customer: Mapped["object"] = relationship("Customer", foreign_keys=[customer_id])

    @property
    def is_abnormal(self) -> bool:
        return self.abnormal_flag == 1

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VisitRecord {self.record_no} status={self.status}>"


class VisitAttachment(Base):
    """拜访附件（需求文档 3.3 表 3）。

    ``capture_lng/capture_lat/captured_at`` 是关键防作弊字段：由客户端从
    EXIF 读取，服务端校验，**不允许前端直接提交**（需求文档 3.3 注）。
    """

    __tablename__ = "visit_attachment"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(
        FKType, ForeignKey("visit_record.id"), nullable=False, index=True
    )
    file_type: Mapped[int] = mapped_column(Integer, nullable=False)
    file_url: Mapped[str] = mapped_column(String(500), nullable=False)
    thumb_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)

    captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    capture_lng: Mapped[float | None] = mapped_column(Numeric(10, 7), nullable=True)
    capture_lat: Mapped[float | None] = mapped_column(Numeric(10, 7), nullable=True)
    watermark: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    record: Mapped["VisitRecord"] = relationship("VisitRecord", back_populates="attachments")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VisitAttachment {self.id} type={self.file_type}>"
