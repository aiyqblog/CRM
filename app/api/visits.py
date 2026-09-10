"""拜访计划与拜访记录接口。

注意路由声明顺序：``/abnormal``、``/sync`` 等字面量路径必须声明在
``/{record_id}`` 之前，否则 FastAPI 会尝试把它们当作整数主键解析，返回 422。
"""

from __future__ import annotations

import os

from fastapi import APIRouter, File, Response, UploadFile
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.api.schemas import CheckinIn, CheckoutIn, PlanIn
from app.api.serializers import attachment_out, plan_out, record_out
from app.config import settings
from app.models import Customer, VisitPlan, VisitRecord
from app.services import exif, permission, storage, visit
from app.services.errors import NotFound, PermissionDenied, ValidationFailed

router = APIRouter(prefix="/api", tags=["visits"])


def _get_customer(db, user, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise NotFound("客户不存在")
    scope = permission.resolve_scope(db, user)
    permission.decide_read(user, customer.owner_id, scope).raise_if_denied()
    return customer


def _get_record(db, user, record_id: int) -> VisitRecord:
    record = visit.get_record(db, record_id)
    scope = permission.resolve_scope(db, user)
    permission.decide_read(user, record.owner_id, scope).raise_if_denied()
    return record


# ==========================================================================
# 拜访计划
# ==========================================================================
@router.get("/visit-plans")
def list_plans(
    user: CurrentUser,
    db: DbSession,
    customer_id: int | None = None,
    status: int | None = None,
    limit: int = 50,
) -> dict:
    scope = permission.resolve_scope(db, user)
    conditions = []
    if not scope.is_full:
        conditions.append(VisitPlan.owner_id.in_(list(scope.visible_user_ids or [])))
    if customer_id is not None:
        conditions.append(VisitPlan.customer_id == customer_id)
    if status is not None:
        conditions.append(VisitPlan.status == status)

    rows = (
        db.execute(
            select(VisitPlan)
            .where(*conditions)
            .order_by(VisitPlan.plan_start.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return {"items": [plan_out(p) for p in rows], "total": len(rows)}


@router.post("/visit-plans", status_code=201)
def create_plan(payload: PlanIn, user: CurrentUser, db: DbSession) -> dict:
    customer = _get_customer(db, user, payload.customer_id)
    plan = visit.create_plan(
        db,
        user=user,
        customer=customer,
        plan_start=payload.plan_start,
        plan_end=payload.plan_end,
        visit_purpose=payload.visit_purpose,
        visit_type=payload.visit_type,
        project_id=payload.project_id,
        address=payload.address,
        longitude=payload.longitude,
        latitude=payload.latitude,
        remind_before=payload.remind_before,
    )
    db.commit()
    db.refresh(plan)
    return plan_out(plan)


@router.post("/visit-plans/{plan_id}/cancel")
def cancel_plan(plan_id: int, payload: dict, user: CurrentUser, db: DbSession) -> dict:
    plan = db.get(VisitPlan, plan_id)
    if plan is None:
        raise NotFound("拜访计划不存在")
    scope = permission.resolve_scope(db, user)
    permission.decide_read(user, plan.owner_id, scope).raise_if_denied()

    visit.cancel_plan(db, user=user, plan=plan, reason=str(payload.get("reason", "")))
    db.commit()
    db.refresh(plan)
    return plan_out(plan)


# ==========================================================================
# 拜访记录
# ==========================================================================
@router.get("/visits")
def list_visits(
    user: CurrentUser,
    db: DbSession,
    customer_id: int | None = None,
    project_id: int | None = None,
    owner_id: int | None = None,
    visit_type: int | None = None,
    customer_keyword: str | None = None,
    abnormal_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    rows, total = visit.list_records(
        db,
        user=user,
        customer_id=customer_id,
        project_id=project_id,
        owner_id=owner_id,
        visit_type=visit_type,
        customer_keyword=customer_keyword,
        abnormal_only=abnormal_only,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [record_out(r, with_attachments=False) for r in rows],
        "total": total,
    }


@router.get("/visits/abnormal")
def abnormal_queue(user: CurrentUser, db: DbSession, limit: int = 100) -> dict:
    """异常复核队列（需求文档 3.5 V-11）。主管及以上可见全团队。"""
    rows, total = visit.list_records(db, user=user, abnormal_only=True, limit=limit)
    return {
        "items": [record_out(r, with_attachments=False) for r in rows],
        "total": total,
    }


@router.post("/visits/sync")
def sync_offline(payload: dict, user: CurrentUser, db: DbSession) -> dict:
    """离线补传（需求文档 3.5 V-10）。"""
    record_ids = [int(x) for x in payload.get("record_ids", [])]
    count = visit.sync_offline_records(db, user=user, record_ids=record_ids)
    db.commit()
    return {"synced": count}


@router.post("/visits/checkin", status_code=201)
def checkin(payload: CheckinIn, user: CurrentUser, db: DbSession) -> dict:
    customer = _get_customer(db, user, payload.customer_id)

    plan = None
    if payload.plan_id is not None:
        plan = db.get(VisitPlan, payload.plan_id)
        if plan is None:
            raise NotFound("拜访计划不存在")

    record = visit.checkin(
        db,
        user=user,
        customer=customer,
        plan=plan,
        project_id=payload.project_id,
        lng=payload.longitude,
        lat=payload.latitude,
        address=payload.address,
        checkin_time=payload.checkin_time,
        is_mocked=payload.is_mocked,
        offline=payload.offline,
        client_time=payload.client_time,
        visit_type=payload.visit_type,
    )
    db.commit()
    db.refresh(record)
    return record_out(record)


@router.post("/visits/{record_id}/checkout")
def checkout(record_id: int, payload: CheckoutIn, user: CurrentUser, db: DbSession) -> dict:
    record = _get_record(db, user, record_id)
    visit.checkout(
        db,
        user=user,
        record=record,
        content=payload.content,
        checkout_time=payload.checkout_time,
        customer_feedback=payload.customer_feedback,
        next_action=payload.next_action,
        next_visit_date=payload.next_visit_date,
        location_note=payload.location_note,
    )
    db.commit()
    db.refresh(record)
    return record_out(record)


@router.post("/visits/{record_id}/attachments", status_code=201)
async def upload_attachment(
    record_id: int,
    user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(...),
) -> dict:
    """上传附件。

    拍摄时间与坐标由**服务端从 EXIF 解析**，不接受请求参数传入
    （需求文档 3.3 表 3 注）。当前端无法提供 EXIF 时（例如相册转存），
    字段为空并由后续定位比对去判定，而不是信任前端自报。
    """
    record = _get_record(db, user, record_id)

    data = await file.read()
    filename = file.filename or "upload.bin"
    file_type = storage.guess_file_type(filename)

    if len(data) > settings.max_upload_bytes:
        raise ValidationFailed(
            "R-08",
            f"单个附件不得超过 {settings.max_upload_bytes // (1024 * 1024)}MB",
        )

    captured_at = capture_lat = capture_lng = None
    stored = data

    if file_type == 1:
        meta = exif.extract_capture_metadata(data)
        captured_at = meta.captured_at
        capture_lat = meta.latitude
        capture_lng = meta.longitude
        # R-09：生成压缩版本用于快速加载
        stored = storage.compress_image(data, 500 * 1024)

    file_url, size = storage.save_bytes(
        # 子目录必须是**单个**路径段：下载路由是 /api/files/{subdir}/{filename}，
        # 传 "visit/1" 这种带斜杠的值会让路径变成三段，路由匹配不上而 404。
        stored, filename=filename, subdir=f"visit_{record.id}"
    )

    watermark = storage.build_watermark(
        captured_at=captured_at,
        latitude=capture_lat,
        longitude=capture_lng,
        user_name=user.display_name,
        customer_name=record.customer.name if record.customer else "",
    )

    attachment = visit.add_attachment(
        db,
        record=record,
        file_type=file_type,
        file_url=file_url,
        file_size=size,
        captured_at=captured_at,
        capture_lng=capture_lng,
        capture_lat=capture_lat,
        watermark=watermark,
    )
    db.commit()
    db.refresh(attachment)

    payload = attachment_out(attachment)
    payload["download_url"] = storage.sign_download(attachment.file_url)
    payload["exif_gps_found"] = capture_lat is not None
    return payload


@router.get("/visits/{record_id}")
def get_visit(record_id: int, user: CurrentUser, db: DbSession) -> dict:
    record = _get_record(db, user, record_id)
    return record_out(record)


@router.delete("/visits/{record_id}")
def delete_visit(record_id: int, user: CurrentUser, db: DbSession) -> dict:
    record = _get_record(db, user, record_id)
    visit.soft_delete(db, user=user, record=record)
    db.commit()
    return {"ok": True, "record_id": record_id}


# ==========================================================================
# 附件下载（时效签名）
# ==========================================================================
@router.get("/files/{subdir}/{filename}")
def download_file(
    subdir: str,
    filename: str,
    e: int,
    s: str,
    user: CurrentUser,
) -> Response:
    file_url = f"/uploads/{subdir}/{filename}"
    if not storage.verify_download(file_url, e, s):
        raise PermissionDenied("下载链接已失效", code="FILE-403")

    path = storage.resolve_local_path(file_url)
    if not os.path.exists(path):
        raise NotFound("文件不存在")

    with open(path, "rb") as handle:
        content = handle.read()

    media_type = "image/jpeg"
    if filename.lower().endswith(".png"):
        media_type = "image/png"
    elif filename.lower().endswith((".mp3", ".m4a")):
        media_type = "audio/mpeg"

    return Response(content=content, media_type=media_type)
