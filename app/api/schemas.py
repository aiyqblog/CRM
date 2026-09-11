"""请求体模型。

只做「形状与类型」校验；业务规则一律在 services 层实现，
避免校验逻辑散落在两处（这是最常见的需求漂移来源）。
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str
    password: str


class CustomerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str | None = None
    industry: str | None = None
    level: str | None = None
    address: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    contact_name: str | None = None
    contact_phone: str | None = None


class PlanIn(BaseModel):
    customer_id: int
    plan_start: datetime
    plan_end: datetime
    visit_purpose: str = Field(min_length=1, max_length=500)
    visit_type: int = 2
    project_id: int | None = None
    address: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    remind_before: int = 30


class CheckinIn(BaseModel):
    customer_id: int
    longitude: float
    latitude: float
    address: str = Field(min_length=1, max_length=255)
    plan_id: int | None = None
    project_id: int | None = None
    visit_type: int | None = None
    checkin_time: datetime | None = None
    #: 客户端上报的模拟定位标记（真机由 APP 检测）
    is_mocked: bool = False
    offline: bool = False
    client_time: datetime | None = None


class CheckoutIn(BaseModel):
    content: str
    checkout_time: datetime | None = None
    customer_feedback: str | None = None
    next_action: str | None = None
    next_visit_date: date | None = None
    location_note: str | None = None


class ProjectIn(BaseModel):
    project_name: str = Field(min_length=1, max_length=200)
    customer_id: int
    end_customer_id: int | None = None
    channel_type: int = 1
    project_type: int = 1
    product_lines: list[str] | None = None
    applied_industry: str | None = None
    #: Issue #5 新增：项目类别（R-31）、产品系列/型号（R-31）、预计 DWIN 日期
    project_category: int | None = None
    product_series: str | None = None
    product_model: str | None = None
    expected_dwin_date: date | None = None
    unit_price: float | None = None
    est_annual_qty: int | None = None
    lifecycle_years: int | None = None
    currency: str = "CNY"
    competitor: str | None = None
    expected_sign_date: date | None = None
    expected_mp_date: date | None = None


class ProjectUpdateIn(BaseModel):
    project_name: str | None = None
    #: 必须暴露该字段，否则 R-30（终端客户锁定后不可改）永远触发不到
    end_customer_id: int | None = None
    unit_price: float | None = None
    est_annual_qty: int | None = None
    lifecycle_years: int | None = None
    competitor: str | None = None
    expected_sign_date: date | None = None
    expected_mp_date: date | None = None
    applied_industry: str | None = None
    #: R-26：金额变更超阈值时需带此标记表示已获审批
    approval_granted: bool = False


class AdvanceIn(BaseModel):
    override_reason: str | None = None
    remark: str | None = None


class RollbackIn(BaseModel):
    reason: str
    remark: str | None = None


class CloseIn(BaseModel):
    close_type: int
    reason: str
    lost_to_competitor: str | None = None


class GateItemIn(BaseModel):
    content: str | None = None
    file_url: str | None = None
    confirmed: bool = False


class ReportIn(BaseModel):
    end_customer_id: int


class AdvanceWithVisitIn(BaseModel):
    """从拜访记录推进项目阶段（需求文档 5.2 联动场景一）。"""

    content: str
    checkout_time: datetime | None = None
    customer_feedback: str | None = None
    next_action: str | None = None
    override_reason: str | None = None
