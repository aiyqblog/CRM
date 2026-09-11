"""销售项目、阶段历史、阶段产出物、项目-拜访关联。

对应需求文档 4.3 节的四张表。阶段模型定义在 ``app.constants``，
门禁校验逻辑在 ``app.services.gate``。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants import (
    ChannelType,
    CloseType,
    ProjectStage,
    ProjectStatus,
    StageAction,
)
from app.models.base import Base, FKType, PKType, utcnow


class SalesProject(Base):
    """销售项目（需求文档 4.3 表 4）。

    金额采用双维度口径：单价 × 年用量 = 年营收；年营收 × 生命周期 = 总营收。
    两个派生字段由服务层计算写入，不接受前端直接提交。
    """

    __tablename__ = "sales_project"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    project_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    project_name: Mapped[str] = mapped_column(String(200), nullable=False)

    customer_id: Mapped[int] = mapped_column(
        FKType, ForeignKey("customer.id"), nullable=False, index=True
    )
    end_customer_id: Mapped[int | None] = mapped_column(
        FKType, ForeignKey("customer.id"), nullable=True
    )
    end_customer_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    owner_id: Mapped[int] = mapped_column(FKType, ForeignKey("crm_user.id"), nullable=False)
    team_id: Mapped[int | None] = mapped_column(FKType, ForeignKey("team.id"), nullable=True)
    team_members: Mapped[list | None] = mapped_column(JSON, nullable=True)

    channel_type: Mapped[int] = mapped_column(Integer, nullable=False, default=ChannelType.DIRECT)
    project_type: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    product_lines: Mapped[list | None] = mapped_column(JSON, nullable=True)
    applied_industry: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: 项目类别（Issue #5）：大型 / 小型。与 project_type（项目类型）不是一回事。
    #: 可空 —— 历史数据由迁移回填默认值，不阻塞读取。
    project_category: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 产品系列与型号（Issue #5）。R-31：型号必须隶属所选系列，服务端二次校验。
    product_series: Mapped[str | None] = mapped_column(String(64), nullable=True)
    product_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 预计 DWIN 日期（Issue #5）。客户口径的里程碑日期，与签约/量产日期并列。
    expected_dwin_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    stage: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ProjectStage.OPPORTUNITY.value
    )
    stage_enter_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    stage_stay_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage_stagnant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: 由阶段自动带出（STAGE_WIN_RATE），不接受人工填写
    win_rate: Mapped[int] = mapped_column(Integer, nullable=False, default=10)

    unit_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    est_annual_qty: Mapped[int | None] = mapped_column(FKType, nullable=True)
    lifecycle_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    est_annual_revenue: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    est_total_revenue: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")

    competitor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    competitor_status: Mapped[str | None] = mapped_column(String(200), nullable=True)

    expected_sign_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_mp_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    close_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    lost_to_competitor: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[int] = mapped_column(Integer, nullable=False, default=ProjectStatus.ONGOING)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_by: Mapped[int | None] = mapped_column(FKType, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    updated_by: Mapped[int | None] = mapped_column(FKType, nullable=True)

    customer: Mapped["object"] = relationship("Customer", foreign_keys=[customer_id])
    owner: Mapped["object"] = relationship("User", foreign_keys=[owner_id])
    gate_items: Mapped[list["ProjectGateItem"]] = relationship(
        "ProjectGateItem", back_populates="project", cascade="all, delete-orphan"
    )
    stage_history: Mapped[list["ProjectStageHistory"]] = relationship(
        "ProjectStageHistory", back_populates="project", cascade="all, delete-orphan"
    )

    @property
    def stage_enum(self) -> ProjectStage:
        return ProjectStage(self.stage)

    @property
    def is_closed(self) -> bool:
        return self.status == ProjectStatus.CLOSED

    @property
    def is_won(self) -> bool:
        return self.close_type == CloseType.WON

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SalesProject {self.project_no} stage={self.stage}>"


class ProjectStageHistory(Base):
    """阶段历史（需求文档 4.3 表 5）。

    仅追加，不可修改 —— 它是阶段停留分析与漏斗转化的唯一数据源。
    """

    __tablename__ = "project_stage_history"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        FKType, ForeignKey("sales_project.id"), nullable=False, index=True
    )
    from_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[int] = mapped_column(Integer, nullable=False, default=StageAction.ADVANCE)

    stay_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gate_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    override_by: Mapped[int | None] = mapped_column(FKType, nullable=True)
    override_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    operator_id: Mapped[int] = mapped_column(FKType, ForeignKey("crm_user.id"), nullable=False)
    operated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    remark: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    project: Mapped["SalesProject"] = relationship(
        "SalesProject", back_populates="stage_history"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StageHistory {self.from_stage}->{self.to_stage} action={self.action}>"


class ProjectGateItem(Base):
    """阶段产出物（需求文档 4.3 表 6）。项目创建时按 GATE_ITEMS 预生成。"""

    __tablename__ = "project_gate_item"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        FKType, ForeignKey("sales_project.id"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    item_code: Mapped[str] = mapped_column(String(32), nullable=False)
    item_name: Mapped[str] = mapped_column(String(100), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_by: Mapped[int | None] = mapped_column(FKType, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped["SalesProject"] = relationship("SalesProject", back_populates="gate_items")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<GateItem {self.item_code} status={self.status}>"


class ProjectVisitRel(Base):
    """项目-拜访关联（需求文档 4.3 表 7）。"""

    __tablename__ = "project_visit_rel"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        FKType, ForeignKey("sales_project.id"), nullable=False, index=True
    )
    visit_record_id: Mapped[int] = mapped_column(
        FKType, ForeignKey("visit_record.id"), nullable=False, index=True
    )
    #: 该拜访是否为阶段推进依据（需求文档 5.2）
    is_stage_trigger: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProjectVisitRel p={self.project_id} v={self.visit_record_id}>"
