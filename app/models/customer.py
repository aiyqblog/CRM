"""客户主数据与终端客户报备。

客户是拜访记录与销售项目的共享主干（需求文档第 5 章）。
报备表独立于客户表，因为「同一终端客户可被不同项目报备，但同一时刻只能有一个
生效报备」的唯一性约束需要落到报备记录上（需求文档 4.7）。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, FKType, PKType, utcnow


class Customer(Base):
    """客户（直接交易方：整机厂 / 方案商 / 代理商）。"""

    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    owner_id: Mapped[int] = mapped_column(FKType, ForeignKey("crm_user.id"), nullable=False)
    team_id: Mapped[int | None] = mapped_column(FKType, ForeignKey("team.id"), nullable=True)

    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    level: Mapped[str | None] = mapped_column(String(16), nullable=True)

    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    longitude: Mapped[float | None] = mapped_column(Numeric(10, 7), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Numeric(10, 7), nullable=True)

    contact_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: 报备锁定后不可修改终端客户字段（R-30）
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_by: Mapped[int | None] = mapped_column(FKType, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    updated_by: Mapped[int | None] = mapped_column(FKType, nullable=True)

    owner: Mapped["object"] = relationship("User", foreign_keys=[owner_id])

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Customer {self.id} {self.name}>"


class CustomerReport(Base):
    """终端客户报备（需求文档 4.7）。

    唯一性由 ``app.services.report`` 在业务层校验：同一 ``end_customer_id``
    在同一时间只允许存在一条 ``status=ACTIVE`` 的记录。
    """

    __tablename__ = "customer_report"

    STATUS_ACTIVE = 1
    STATUS_RELEASED = 2

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    end_customer_id: Mapped[int] = mapped_column(
        FKType, ForeignKey("customer.id"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        FKType, ForeignKey("sales_project.id"), nullable=False, index=True
    )
    owner_id: Mapped[int] = mapped_column(FKType, ForeignKey("crm_user.id"), nullable=False)

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    expire_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: 进入 Design-in 后转锁定，不再自动释放
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=STATUS_ACTIVE)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_by: Mapped[int | None] = mapped_column(FKType, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<CustomerReport {self.id} customer={self.end_customer_id} "
            f"project={self.project_id} status={self.status}>"
        )
