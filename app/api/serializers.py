"""ORM → JSON 序列化。

集中在此，避免各路由各写一套导致前端拿到的字段名不一致。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.constants import (
    CHANNEL_TYPE_LABELS,
    CLOSE_TYPE_LABELS,
    LOCATION_STATUS_LABELS,
    PLAN_STATUS_LABELS,
    PRODUCT_SERIES_MODELS,
    PROJECT_CATEGORY_LABELS,
    PROJECT_TYPE_LABELS,
    RECORD_STATUS_LABELS,
    ROLE_LABELS,
    STAGE_LABELS,
    STAGE_TYPICAL_DAYS,
    VISIT_TYPE_LABELS,
    ChannelType,
    CloseType,
    LocationStatus,
    PlanStatus,
    ProjectCategory,
    ProjectStage,
    ProjectStatus,
    ProjectType,
    RecordStatus,
    VisitType,
)
from app.models import (
    Customer,
    CustomerReport,
    ProjectGateItem,
    ProjectStageHistory,
    SalesProject,
    User,
    VisitAttachment,
    VisitPlan,
    VisitRecord,
)


def _iso(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def user_out(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "role_label": ROLE_LABELS.get(user.role_enum, user.role),
        "team_id": user.team_id,
        "manager_id": user.manager_id,
    }


def customer_out(customer: Customer) -> dict:
    return {
        "id": customer.id,
        "name": customer.name,
        "code": customer.code,
        "owner_id": customer.owner_id,
        "team_id": customer.team_id,
        "industry": customer.industry,
        "level": customer.level,
        "address": customer.address,
        "longitude": float(customer.longitude) if customer.longitude is not None else None,
        "latitude": float(customer.latitude) if customer.latitude is not None else None,
        "contact_name": customer.contact_name,
        "contact_phone": customer.contact_phone,
        "is_locked": customer.is_locked,
        "created_at": _iso(customer.created_at),
    }


def plan_out(plan: VisitPlan) -> dict:
    return {
        "id": plan.id,
        "plan_no": plan.plan_no,
        "owner_id": plan.owner_id,
        "customer_id": plan.customer_id,
        "customer_name": plan.customer.name if plan.customer else None,
        "project_id": plan.project_id,
        "plan_start": _iso(plan.plan_start),
        "plan_end": _iso(plan.plan_end),
        "visit_type": plan.visit_type,
        "visit_type_label": VISIT_TYPE_LABELS.get(VisitType(plan.visit_type)),
        "visit_purpose": plan.visit_purpose,
        "address": plan.address,
        "status": plan.status,
        "status_label": PLAN_STATUS_LABELS.get(PlanStatus(plan.status)),
        "cancel_reason": plan.cancel_reason,
    }


def attachment_out(attachment: VisitAttachment) -> dict:
    return {
        "id": attachment.id,
        "record_id": attachment.record_id,
        "file_type": attachment.file_type,
        "file_url": attachment.file_url,
        "thumb_url": attachment.thumb_url,
        "file_size": attachment.file_size,
        "duration": attachment.duration,
        "captured_at": _iso(attachment.captured_at),
        "capture_lng": float(attachment.capture_lng) if attachment.capture_lng is not None else None,
        "capture_lat": float(attachment.capture_lat) if attachment.capture_lat is not None else None,
        "watermark": attachment.watermark,
    }


def record_out(record: VisitRecord, *, with_attachments: bool = True) -> dict:
    data = {
        "id": record.id,
        "record_no": record.record_no,
        "plan_id": record.plan_id,
        "customer_id": record.customer_id,
        "customer_name": record.customer.name if record.customer else None,
        "project_id": record.project_id,
        "owner_id": record.owner_id,
        "visit_type": record.visit_type,
        "visit_type_label": VISIT_TYPE_LABELS.get(VisitType(record.visit_type)),
        "checkin_time": _iso(record.checkin_time),
        "checkin_lng": float(record.checkin_lng),
        "checkin_lat": float(record.checkin_lat),
        "checkin_address": record.checkin_address,
        "checkout_time": _iso(record.checkout_time),
        "duration_min": record.duration_min,
        "location_status": record.location_status,
        "location_status_label": LOCATION_STATUS_LABELS.get(
            LocationStatus(record.location_status)
        ),
        "distance_m": record.distance_m,
        "location_note": record.location_note,
        "content": record.content,
        "customer_feedback": record.customer_feedback,
        "receptionist": record.receptionist,
        "next_action": record.next_action,
        "next_visit_date": _iso(record.next_visit_date),
        "project_stage_after": record.project_stage_after,
        "status": record.status,
        "status_label": RECORD_STATUS_LABELS.get(RecordStatus(record.status)),
        "abnormal_flag": record.abnormal_flag,
        "abnormal_reason": record.abnormal_reason,
        "sync_status": record.sync_status,
    }
    if with_attachments:
        data["attachments"] = [attachment_out(a) for a in record.attachments]
    return data


def project_out(project: SalesProject, *, detail: bool = False) -> dict:
    stage = project.stage_enum
    data = {
        "id": project.id,
        "project_no": project.project_no,
        "project_name": project.project_name,
        "customer_id": project.customer_id,
        "customer_name": project.customer.name if project.customer else None,
        "end_customer_id": project.end_customer_id,
        "end_customer_locked": project.end_customer_locked,
        "owner_id": project.owner_id,
        "channel_type": project.channel_type,
        "channel_type_label": CHANNEL_TYPE_LABELS.get(ChannelType(project.channel_type)),
        "project_type": project.project_type,
        "project_type_label": PROJECT_TYPE_LABELS.get(ProjectType(project.project_type)),
        "product_lines": project.product_lines,
        "applied_industry": project.applied_industry,
        "project_category": project.project_category,
        "project_category_label": (
            PROJECT_CATEGORY_LABELS.get(ProjectCategory(project.project_category))
            if project.project_category
            else None
        ),
        "product_series": project.product_series,
        "product_model": project.product_model,
        #: 该系列下允许的全部型号 —— 前端联动下拉与排查「型号为何被拒」都用它
        "product_series_models": PRODUCT_SERIES_MODELS.get(project.product_series or "", []),
        "expected_dwin_date": _iso(project.expected_dwin_date),
        "stage": project.stage,
        "stage_label": STAGE_LABELS.get(stage),
        "stage_enter_time": _iso(project.stage_enter_time),
        "stage_stay_days": project.stage_stay_days,
        "stage_stagnant": project.stage_stagnant,
        "stage_typical_days": STAGE_TYPICAL_DAYS.get(stage),
        "win_rate": project.win_rate,
        "unit_price": float(project.unit_price) if project.unit_price is not None else None,
        "est_annual_qty": project.est_annual_qty,
        "lifecycle_years": project.lifecycle_years,
        "est_annual_revenue": (
            float(project.est_annual_revenue)
            if project.est_annual_revenue is not None
            else None
        ),
        "est_total_revenue": (
            float(project.est_total_revenue) if project.est_total_revenue is not None else None
        ),
        "currency": project.currency,
        "competitor": project.competitor,
        "expected_sign_date": _iso(project.expected_sign_date),
        "expected_mp_date": _iso(project.expected_mp_date),
        "close_type": project.close_type,
        "close_type_label": (
            CLOSE_TYPE_LABELS.get(CloseType(project.close_type))
            if project.close_type
            else None
        ),
        "close_reason": project.close_reason,
        "lost_to_competitor": project.lost_to_competitor,
        "status": project.status,
        "status_label": "进行中" if project.status == ProjectStatus.ONGOING else "已关闭",
        "created_at": _iso(project.created_at),
    }

    if detail:
        data["gate_items"] = [gate_item_out(i) for i in project.gate_items]
        # 阶段历史按时间倒序：前端时间轴与测试都期望 [0] 是最新一条
        data["stage_history"] = [
            stage_history_out(h) for h in sorted(project.stage_history, key=lambda h: h.id, reverse=True)
        ]
    return data


def gate_item_out(item: ProjectGateItem) -> dict:
    return {
        "id": item.id,
        "stage": item.stage,
        "stage_label": STAGE_LABELS.get(ProjectStage(item.stage)),
        "item_code": item.item_code,
        "item_name": item.item_name,
        "required": item.required,
        "status": item.status,
        "status_label": {0: "未提交", 1: "已提交", 2: "已确认"}.get(item.status, "未知"),
        "content": item.content,
        "file_url": item.file_url,
        "submitted_at": _iso(item.submitted_at),
    }


def stage_history_out(history: ProjectStageHistory) -> dict:
    return {
        "id": history.id,
        "from_stage": history.from_stage,
        "from_stage_label": (
            STAGE_LABELS.get(ProjectStage(history.from_stage)) if history.from_stage else None
        ),
        "to_stage": history.to_stage,
        "to_stage_label": STAGE_LABELS.get(ProjectStage(history.to_stage)),
        "action": history.action,
        "stay_days": history.stay_days,
        "gate_passed": history.gate_passed,
        "override_by": history.override_by,
        "override_reason": history.override_reason,
        "operator_id": history.operator_id,
        "operated_at": _iso(history.operated_at),
        "remark": history.remark,
    }


def report_out(report: CustomerReport) -> dict:
    return {
        "id": report.id,
        "end_customer_id": report.end_customer_id,
        "project_id": report.project_id,
        "owner_id": report.owner_id,
        "start_date": _iso(report.start_date),
        "expire_date": _iso(report.expire_date),
        "is_locked": report.is_locked,
        "status": report.status,
    }
