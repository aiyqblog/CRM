"""拜访业务规则与流程编排。

需求文档 3.6 节 R-01~R-13。

设计约定：**规则判定写成纯函数，流程编排写成接受 db 会话的函数**。
纯函数是单元测试的主要目标（tests/unit/test_visit_rules.py），
编排函数由接口测试覆盖（tests/api/test_visits.py）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import (
    FileType,
    LocationStatus,
    PlanStatus,
    RecordStatus,
    SyncStatus,
    VisitType,
)
from app.models import Customer, ProjectVisitRel, VisitAttachment, VisitPlan, VisitRecord
from app.models.base import utcnow
from app.services import geo, permission, sequence
from app.services.errors import Conflict, NotFound, ValidationFailed

ABNORMAL_DURATION = "拜访时长短于阈值"
ABNORMAL_MOCKED = "检测到模拟定位"
ABNORMAL_DRIFT = "离线补传时间偏差过大"


# ==========================================================================
# 纯规则函数
# ==========================================================================
def check_checkin_time(
    plan_start: datetime | None, checkin_time: datetime, tolerance_min: int
) -> None:
    """R-01：签到不得早于计划开始时间 ``tolerance_min`` 分钟前。

    无计划（临时拜访）时跳过。
    """
    if plan_start is None:
        return
    earliest = plan_start - timedelta(minutes=tolerance_min)
    if checkin_time < earliest:
        raise ValidationFailed(
            "R-01",
            f"签到时间不得早于计划开始时间前 {tolerance_min} 分钟",
            details={
                "plan_start": plan_start.isoformat(),
                "earliest": earliest.isoformat(),
                "checkin_time": checkin_time.isoformat(),
            },
        )


def check_checkout_order(checkin_time: datetime, checkout_time: datetime) -> None:
    """R-02：签退时间必须晚于签到时间。"""
    if checkout_time <= checkin_time:
        raise ValidationFailed(
            "R-02",
            "签退时间必须晚于签到时间",
            details={
                "checkin_time": checkin_time.isoformat(),
                "checkout_time": checkout_time.isoformat(),
            },
        )


def compute_duration_min(checkin_time: datetime, checkout_time: datetime) -> int:
    """拜访时长（分钟，向下取整）。"""
    return int((checkout_time - checkin_time).total_seconds() // 60)


def evaluate_duration(
    checkin_time: datetime, checkout_time: datetime, min_minutes: int
) -> tuple[bool, str | None]:
    """R-05：时长过短不阻断，但标记异常进复核队列。

    返回 ``(是否异常, 原因)``。
    """
    duration = compute_duration_min(checkin_time, checkout_time)
    if duration < min_minutes:
        return True, ABNORMAL_DURATION
    return False, None


def check_content(content: str | None, min_chars: int) -> None:
    """R-11：拜访纪要字数下限。"""
    length = len((content or "").strip())
    if length < min_chars:
        raise ValidationFailed(
            "R-11",
            f"拜访纪要至少 {min_chars} 字，当前 {length} 字",
            details={"min_chars": min_chars, "actual": length},
        )


def check_location_note(location_status: int, note: str | None) -> None:
    """R-03：超出围栏时强制填写说明（不阻断提交，但必须有说明）。"""
    if location_status == LocationStatus.OUT_OF_FENCE and not (note or "").strip():
        raise ValidationFailed(
            "R-03", "签到位置超出客户地址围栏，必须填写说明"
        )


def check_attachment_limits(
    *,
    existing_images: int,
    existing_voices: int,
    incoming_type: int,
    incoming_size: int,
    max_images: int,
    max_voices: int,
    max_bytes: int,
) -> None:
    """R-08：附件数量与体积上限。"""
    if incoming_size > max_bytes:
        raise ValidationFailed(
            "R-08",
            f"单个附件不得超过 {max_bytes // (1024 * 1024)}MB",
            details={"size": incoming_size, "max": max_bytes},
        )

    if incoming_type == FileType.IMAGE and existing_images + 1 > max_images:
        raise ValidationFailed(
            "R-08",
            f"单条拜访记录最多 {max_images} 张图片",
            details={"existing": existing_images, "max": max_images},
        )

    if incoming_type == FileType.VOICE and existing_voices + 1 > max_voices:
        raise ValidationFailed("R-08", f"单条拜访记录最多 {max_voices} 段语音")


def evaluate_offline_drift(
    client_time: datetime, server_time: datetime, threshold_min: int
) -> tuple[bool, str | None]:
    """R-10：离线补传时以设备时间为准，但偏差过大要标记异常。

    返回 ``(是否异常, 原因)``。
    """
    drift = abs((server_time - client_time).total_seconds()) / 60.0
    if drift > threshold_min:
        return True, ABNORMAL_DRIFT
    return False, None


def evaluate_mocked_location(is_mocked: bool) -> tuple[bool, str | None]:
    """R-07：模拟定位标记异常并强制主管复核。"""
    if is_mocked:
        return True, ABNORMAL_MOCKED
    return False, None


# ==========================================================================
# 流程编排
# ==========================================================================
def get_record(db: Session, record_id: int) -> VisitRecord:
    record = db.get(VisitRecord, record_id)
    if record is None or record.is_deleted:
        raise NotFound("拜访记录不存在")
    return record


def find_overlapping(
    db: Session,
    owner_id: int,
    start: datetime,
    end: datetime | None,
    exclude_id: int | None = None,
) -> VisitRecord | None:
    """R-04：查找同一拜访人时间重叠的记录。

    未签退的记录视为区间右端为无穷大。
    """
    effective_end = end or datetime.max
    stmt = select(VisitRecord).where(
        VisitRecord.owner_id == owner_id,
        VisitRecord.is_deleted == 0,
    )
    if exclude_id is not None:
        stmt = stmt.where(VisitRecord.id != exclude_id)

    for existing in db.execute(stmt).scalars().all():
        existing_end = existing.checkout_time or datetime.max
        if start < existing_end and existing.checkin_time < effective_end:
            return existing
    return None


def checkin(
    db: Session,
    *,
    user,
    customer: Customer,
    plan: VisitPlan | None = None,
    project_id: int | None = None,
    lng: float,
    lat: float,
    address: str,
    checkin_time: datetime | None = None,
    is_mocked: bool = False,
    offline: bool = False,
    client_time: datetime | None = None,
    visit_type: int | None = None,
) -> VisitRecord:
    """签到：创建一条进行中的拜访记录。

    校验顺序刻意设计为「先时间、再重叠、后定位」——时间类错误是硬错误必须拦，
    定位类问题只做标记不阻断（现场信号差是常态，阻断会让销售用不了系统）。
    """
    if not permission.can_create(user):
        raise ValidationFailed("PERM-403", "当前角色不允许新建拜访记录")

    now = utcnow()
    if offline and client_time is not None:
        # 离线补传以设备时间为准，但要记录与服务器时间的偏差
        effective_time = client_time
    else:
        effective_time = checkin_time or now

    if plan is not None:
        if plan.status == PlanStatus.CANCELLED:
            raise ValidationFailed("PLAN-01", "该拜访计划已取消，无法签到")
        check_checkin_time(
            plan.plan_start, effective_time, settings.checkin_early_tolerance_min
        )
        if plan.owner_id != user.id and user.role_enum.value != "admin":
            raise ValidationFailed("PLAN-02", "只能对自己负责的拜访计划签到")

    # R-04
    overlap = find_overlapping(db, user.id, effective_time, None)
    if overlap is not None:
        raise Conflict(
            "R-04",
            "同一时段已存在未结束的拜访记录，请先签退",
            details={"conflict_record_no": overlap.record_no},
        )

    location_status, distance_m = geo.evaluate_location(
        customer_lat=customer.latitude,
        customer_lng=customer.longitude,
        checkin_lat=lat,
        checkin_lng=lng,
        fence_meters=settings.fence_meters,
        is_mocked=is_mocked,
    )

    abnormal_flag = 0
    abnormal_reason: str | None = None
    is_abnormal, reason = evaluate_mocked_location(is_mocked)
    if is_abnormal:
        abnormal_flag, abnormal_reason = 1, reason

    if offline and client_time is not None:
        drift_abnormal, drift_reason = evaluate_offline_drift(
            client_time, now, settings.offline_time_drift_min
        )
        if drift_abnormal:
            abnormal_flag = 1
            abnormal_reason = (
                f"{abnormal_reason}；{drift_reason}" if abnormal_reason else drift_reason
            )

    record = VisitRecord(
        record_no=sequence.next_visit_record_no(db, now),
        plan_id=plan.id if plan else None,
        customer_id=customer.id,
        project_id=project_id,
        owner_id=user.id,
        visit_type=visit_type if visit_type is not None else (
            plan.visit_type if plan is not None else VisitType.ROUTINE
        ),
        checkin_time=effective_time,
        checkin_lng=lng,
        checkin_lat=lat,
        checkin_address=address,
        location_status=location_status,
        distance_m=distance_m,
        content="",  # 签到时先留空，签退时校验必填
        status=RecordStatus.ONGOING,
        abnormal_flag=abnormal_flag,
        abnormal_reason=abnormal_reason,
        sync_status=SyncStatus.PENDING if offline else SyncStatus.SYNCED,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(record)

    if plan is not None:
        plan.status = PlanStatus.ONGOING
        plan.updated_by = user.id

    db.flush()
    return record


def checkout(
    db: Session,
    *,
    user,
    record: VisitRecord,
    content: str,
    checkout_time: datetime | None = None,
    customer_feedback: str | None = None,
    next_action: str | None = None,
    next_visit_date=None,
    location_note: str | None = None,
    sync_now: bool = True,
) -> VisitRecord:
    """签退并提交拜访记录。"""
    if record.status != RecordStatus.ONGOING:
        raise ValidationFailed(
            "STATE-01", f"当前状态（{record.status}）不允许签退"
        )

    decision = permission.decide_visit_edit(
        user,
        owner_id=record.owner_id,
        checkin_time=record.checkin_time,
        now=utcnow(),
        scope=permission.resolve_scope(db, user),
        window_hours=settings.edit_window_hours,
    )
    decision.raise_if_denied()

    effective_checkout = checkout_time or utcnow()
    check_checkout_order(record.checkin_time, effective_checkout)
    check_content(content, settings.min_content_chars)
    check_location_note(record.location_status, location_note)

    record.checkout_time = effective_checkout
    record.duration_min = compute_duration_min(record.checkin_time, effective_checkout)
    record.content = content.strip()
    record.customer_feedback = customer_feedback
    record.next_action = next_action
    record.next_visit_date = next_visit_date
    record.location_note = location_note
    record.status = RecordStatus.COMPLETED
    if sync_now:
        record.sync_status = SyncStatus.SYNCED
    record.updated_by = user.id

    # R-05
    is_abnormal, reason = evaluate_duration(
        record.checkin_time, effective_checkout, settings.min_visit_minutes
    )
    if is_abnormal:
        record.abnormal_flag = 1
        record.abnormal_reason = (
            f"{record.abnormal_reason}；{reason}"
            if record.abnormal_reason
            else reason
        )

    if record.plan_id:
        plan = db.get(VisitPlan, record.plan_id)
        if plan is not None:
            plan.status = PlanStatus.COMPLETED
            plan.updated_by = user.id

    db.flush()
    return record


def add_attachment(
    db: Session,
    *,
    record: VisitRecord,
    file_type: int,
    file_url: str,
    file_size: int,
    thumb_url: str | None = None,
    duration: int | None = None,
    captured_at: datetime | None = None,
    capture_lng: float | None = None,
    capture_lat: float | None = None,
    watermark: str | None = None,
) -> VisitAttachment:
    """新增附件。校验 R-08 数量与体积上限。

    ``capture_lng/capture_lat/captured_at`` 必须由服务端从 EXIF 读取后传入，
    不接受前端直接提交的坐标（需求文档 3.3 表 3 注）。
    """
    existing = db.execute(
        select(VisitAttachment).where(VisitAttachment.record_id == record.id)
    ).scalars().all()

    images = sum(1 for a in existing if a.file_type == FileType.IMAGE)
    voices = sum(1 for a in existing if a.file_type == FileType.VOICE)

    check_attachment_limits(
        existing_images=images,
        existing_voices=voices,
        incoming_type=file_type,
        incoming_size=file_size,
        max_images=settings.max_images_per_record,
        max_voices=1,
        max_bytes=settings.max_upload_bytes,
    )

    attachment = VisitAttachment(
        record_id=record.id,
        file_type=file_type,
        file_url=file_url,
        thumb_url=thumb_url,
        file_size=file_size,
        duration=duration,
        captured_at=captured_at,
        capture_lng=capture_lng,
        capture_lat=capture_lat,
        watermark=watermark,
    )
    db.add(attachment)
    db.flush()
    return attachment


def is_linked_to_project(db: Session, record_id: int) -> bool:
    """拜访记录是否已关联销售项目（R-13 前置判断）。"""
    if db.get(VisitRecord, record_id) is None:
        return False
    record = db.get(VisitRecord, record_id)
    if record is not None and record.project_id is not None:
        return True
    count = db.execute(
        select(func.count())
        .select_from(ProjectVisitRel)
        .where(ProjectVisitRel.visit_record_id == record_id)
    ).scalar_one()
    return bool(count)


def soft_delete(db: Session, *, user, record: VisitRecord) -> VisitRecord:
    """软删除（R-12 / R-13）。"""
    decision = permission.decide_visit_delete(
        user,
        owner_id=record.owner_id,
        checkin_time=record.checkin_time,
        now=utcnow(),
        window_hours=settings.edit_window_hours,
        linked_project=is_linked_to_project(db, record.id),
    )
    decision.raise_if_denied()

    record.is_deleted = 1
    record.updated_by = user.id
    db.flush()
    return record


def auto_close_stale(db: Session, now: datetime | None = None) -> int:
    """状态机：进行中超过 ``auto_close_hours`` 未签退的记录自动关闭并标记异常。"""
    moment = now or utcnow()
    deadline = moment - timedelta(hours=settings.auto_close_hours)

    stale = db.execute(
        select(VisitRecord).where(
            VisitRecord.status == RecordStatus.ONGOING,
            VisitRecord.checkin_time < deadline,
            VisitRecord.is_deleted == 0,
        )
    ).scalars().all()

    for record in stale:
        record.status = RecordStatus.AUTO_CLOSED
        record.abnormal_flag = 1
        record.abnormal_reason = (
            f"{record.abnormal_reason}；超时未签退自动关闭"
            if record.abnormal_reason
            else "超时未签退自动关闭"
        )

    db.flush()
    return len(stale)


def sync_offline_records(db: Session, *, user, record_ids: list[int]) -> int:
    """离线补传：把待同步记录标记为已同步。

    现实中这里要校验客户端时间与服务端时间的偏差，并处理冲突；
    本地实现只做状态翻转，保留接口形状。
    """
    if not record_ids:
        return 0

    rows = db.execute(
        select(VisitRecord).where(
            VisitRecord.id.in_(record_ids),
            VisitRecord.owner_id == user.id,
            VisitRecord.sync_status == SyncStatus.PENDING,
        )
    ).scalars().all()

    now = utcnow()
    for record in rows:
        record.sync_status = SyncStatus.SYNCED
        abnormal, reason = evaluate_offline_drift(
            record.checkin_time, now, settings.offline_time_drift_min
        )
        if abnormal:
            record.abnormal_flag = 1
            record.abnormal_reason = (
                f"{record.abnormal_reason}；{reason}"
                if record.abnormal_reason
                else reason
            )

    db.flush()
    return len(rows)


def list_records(
    db: Session,
    *,
    user,
    customer_id: int | None = None,
    project_id: int | None = None,
    owner_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    visit_type: int | None = None,
    abnormal_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[VisitRecord], int]:
    """按条件查询拜访记录，自动套用数据权限。"""
    scope = permission.resolve_scope(db, user)

    conditions = [VisitRecord.is_deleted == 0]
    if not scope.is_full:
        conditions.append(VisitRecord.owner_id.in_(list(scope.visible_user_ids or [])))

    if customer_id is not None:
        conditions.append(VisitRecord.customer_id == customer_id)
    if project_id is not None:
        conditions.append(VisitRecord.project_id == project_id)
    if owner_id is not None:
        conditions.append(VisitRecord.owner_id == owner_id)
    if date_from is not None:
        conditions.append(VisitRecord.checkin_time >= date_from)
    if date_to is not None:
        conditions.append(VisitRecord.checkin_time <= date_to)
    if visit_type is not None:
        # 直接过滤记录上的冗余列，而不是子查询 plan：
        # 临时拜访没有 plan，用子查询会全部漏掉。
        conditions.append(VisitRecord.visit_type == visit_type)
    if abnormal_only:
        conditions.append(VisitRecord.abnormal_flag == 1)

    total = db.execute(
        select(func.count()).select_from(VisitRecord).where(*conditions)
    ).scalar_one()

    rows = (
        db.execute(
            select(VisitRecord)
            .where(*conditions)
            .order_by(VisitRecord.checkin_time.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


def create_plan(
    db: Session,
    *,
    user,
    customer: Customer,
    plan_start: datetime,
    plan_end: datetime,
    visit_purpose: str,
    visit_type: int = VisitType.ROUTINE,
    project_id: int | None = None,
    address: str | None = None,
    longitude: float | None = None,
    latitude: float | None = None,
    remind_before: int = 30,
) -> VisitPlan:
    """创建拜访计划。"""
    if not permission.can_create(user):
        raise ValidationFailed("PERM-403", "当前角色不允许新建拜访计划")
    if plan_end < plan_start:
        raise ValidationFailed("PLAN-03", "计划结束时间不得早于开始时间")

    now = utcnow()
    plan = VisitPlan(
        plan_no=sequence.next_visit_plan_no(db, now),
        owner_id=user.id,
        customer_id=customer.id,
        project_id=project_id,
        plan_start=plan_start,
        plan_end=plan_end,
        visit_type=visit_type,
        visit_purpose=visit_purpose,
        address=address or customer.address,
        longitude=longitude if longitude is not None else customer.longitude,
        latitude=latitude if latitude is not None else customer.latitude,
        remind_before=remind_before,
        status=PlanStatus.PENDING,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(plan)
    db.flush()
    return plan


def cancel_plan(db: Session, *, user, plan: VisitPlan, reason: str) -> VisitPlan:
    """取消拜访计划。"""
    if plan.status in (PlanStatus.COMPLETED, PlanStatus.CANCELLED):
        raise ValidationFailed("STATE-02", "已完成或已取消的计划不能再次取消")
    if not reason or not reason.strip():
        raise ValidationFailed("PLAN-04", "取消原因必填")

    plan.status = PlanStatus.CANCELLED
    plan.cancel_reason = reason.strip()
    plan.updated_by = user.id
    db.flush()
    return plan
