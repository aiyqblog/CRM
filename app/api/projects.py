"""销售项目接口。

字面量路由（``/funnel``）必须声明在 ``/{project_id}`` 之前。
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.api.schemas import (
    AdvanceIn,
    CloseIn,
    GateItemIn,
    ProjectIn,
    ProjectUpdateIn,
    ReportIn,
    RollbackIn,
)
from app.api.serializers import (
    gate_item_out,
    project_out,
    record_out,
    report_out,
)
from app.constants import CloseType, ProjectStage
from app.models import Customer, ProjectVisitRel, SalesProject, VisitRecord
from app.services import gate, permission
from app.services import project as project_svc
from app.services import report as report_svc
from app.services.errors import NotFound, ValidationFailed

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _get_project(db, user, project_id: int) -> SalesProject:
    project = project_svc.get_project(db, project_id)
    scope = permission.resolve_scope(db, user)
    permission.decide_read(user, project.owner_id, scope).raise_if_denied()
    return project


def _serialize_detail(db, project: SalesProject, user) -> dict:
    data = project_out(project, detail=True)

    summary = project_svc.stage_summary(db, user)
    data["stage_benchmark"] = next(
        (row for row in summary if row["stage"] == project.stage), None
    )
    data["visit_count"] = db.execute(
        select(func.count())
        .select_from(ProjectVisitRel)
        .where(ProjectVisitRel.project_id == project.id)
    ).scalar_one()
    return data


# ==========================================================================
# 列表与看板
# ==========================================================================
@router.get("")
def list_projects(
    user: CurrentUser,
    db: DbSession,
    stage: str | None = None,
    customer_id: int | None = None,
    stagnant_only: bool = False,
    ongoing_only: bool = False,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    stage_enum = ProjectStage(stage) if stage else None
    rows, total = project_svc.list_projects(
        db,
        user=user,
        stage=stage_enum,
        customer_id=customer_id,
        stagnant_only=stagnant_only,
        ongoing_only=ongoing_only,
        keyword=keyword,
        limit=limit,
        offset=offset,
    )
    return {"items": [project_out(p) for p in rows], "total": total}


@router.get("/funnel")
def funnel(user: CurrentUser, db: DbSession) -> dict:
    """销售漏斗：各阶段项目数、金额与加权营收。"""
    return {"stages": project_svc.stage_summary(db, user)}


# ==========================================================================
# 创建与读取
# ==========================================================================
@router.post("", status_code=201)
def create_project(payload: ProjectIn, user: CurrentUser, db: DbSession) -> dict:
    customer = db.get(Customer, payload.customer_id)
    if customer is None:
        raise NotFound("客户不存在")

    project = project_svc.create_project(
        db,
        user=user,
        project_name=payload.project_name,
        customer=customer,
        channel_type=payload.channel_type,
        project_type=payload.project_type,
        product_lines=payload.product_lines,
        applied_industry=payload.applied_industry,
        project_category=payload.project_category,
        product_series=payload.product_series,
        product_model=payload.product_model,
        expected_dwin_date=payload.expected_dwin_date,
        unit_price=payload.unit_price,
        est_annual_qty=payload.est_annual_qty,
        lifecycle_years=payload.lifecycle_years,
        currency=payload.currency,
        competitor=payload.competitor,
        expected_sign_date=payload.expected_sign_date,
        expected_mp_date=payload.expected_mp_date,
        end_customer_id=payload.end_customer_id,
    )
    db.commit()
    db.refresh(project)
    return _serialize_detail(db, project, user)


@router.get("/{project_id}")
def get_project(project_id: int, user: CurrentUser, db: DbSession) -> dict:
    project = _get_project(db, user, project_id)
    return _serialize_detail(db, project, user)


@router.patch("/{project_id}")
def update_project(
    project_id: int, payload: ProjectUpdateIn, user: CurrentUser, db: DbSession
) -> dict:
    project = _get_project(db, user, project_id)
    fields = payload.model_dump(exclude_none=True, exclude={"approval_granted"})
    project_svc.update_project(
        db,
        user=user,
        project=project,
        approval_granted=payload.approval_granted,
        **fields,
    )
    db.commit()
    db.refresh(project)
    return project_out(project)


# ==========================================================================
# 阶段流转
# ==========================================================================
@router.post("/{project_id}/advance")
def advance(
    project_id: int, payload: AdvanceIn, user: CurrentUser, db: DbSession
) -> dict:
    project = _get_project(db, user, project_id)
    project_svc.advance_stage(
        db,
        user=user,
        project=project,
        override_reason=payload.override_reason,
        remark=payload.remark,
    )
    _maybe_lock_report(db, project)
    db.commit()
    db.refresh(project)
    return project_out(project)


@router.post("/{project_id}/rollback")
def rollback(
    project_id: int, payload: RollbackIn, user: CurrentUser, db: DbSession
) -> dict:
    project = _get_project(db, user, project_id)
    project_svc.rollback_stage(
        db, user=user, project=project, reason=payload.reason, remark=payload.remark
    )
    db.commit()
    db.refresh(project)
    return project_out(project)


@router.post("/{project_id}/close")
def close(project_id: int, payload: CloseIn, user: CurrentUser, db: DbSession) -> dict:
    project = _get_project(db, user, project_id)
    project_svc.close_project(
        db,
        user=user,
        project=project,
        close_type=CloseType(payload.close_type),
        reason=payload.reason,
        lost_to_competitor=payload.lost_to_competitor,
    )
    db.commit()
    db.refresh(project)
    return project_out(project)


@router.post("/{project_id}/reactivate")
def reactivate(project_id: int, user: CurrentUser, db: DbSession) -> dict:
    project = _get_project(db, user, project_id)
    project_svc.reactivate_project(db, user=user, project=project)
    db.commit()
    db.refresh(project)
    return project_out(project)


@router.delete("/{project_id}")
def delete_project(project_id: int, user: CurrentUser, db: DbSession) -> dict:
    project = _get_project(db, user, project_id)
    project_svc.delete_project(db, user=user, project=project)
    db.commit()
    return {"ok": True, "project_id": project_id}


# ==========================================================================
# 产出物
# ==========================================================================
@router.post("/{project_id}/gate-items/{item_code}")
def submit_gate_item(
    project_id: int,
    item_code: str,
    payload: GateItemIn,
    user: CurrentUser,
    db: DbSession,
) -> dict:
    project = _get_project(db, user, project_id)
    if project.is_closed:
        raise ValidationFailed("R-25", "项目已关闭，不可再提交产出物")

    item = gate.mark_item_submitted(
        db,
        project_id=project.id,
        item_code=item_code,
        operator_id=user.id,
        content=payload.content,
        file_url=payload.file_url,
        confirmed=payload.confirmed,
    )
    db.commit()
    db.refresh(item)
    return gate_item_out(item)


@router.get("/{project_id}/gate-check")
def gate_check(project_id: int, user: CurrentUser, db: DbSession) -> dict:
    """查看当前阶段门禁状态，供前端在推进前预检。"""
    project = _get_project(db, user, project_id)
    result = gate.check_gate(db, project)
    return {
        "stage": result.stage.value,
        "passed": result.passed,
        "missing": result.missing,
        "summary": result.summary(),
    }


# ==========================================================================
# 拜访联动
# ==========================================================================
@router.get("/{project_id}/visits")
def project_visits(project_id: int, user: CurrentUser, db: DbSession) -> dict:
    """项目详情页的拜访时间轴（需求文档 5.2 联动场景二）。"""
    project = _get_project(db, user, project_id)
    rows = (
        db.execute(
            select(VisitRecord)
            .where(
                VisitRecord.project_id == project.id,
                VisitRecord.is_deleted == 0,
            )
            .order_by(VisitRecord.checkin_time.desc())
        )
        .scalars()
        .all()
    )
    return {"items": [record_out(r, with_attachments=False) for r in rows]}


# ==========================================================================
# 终端客户报备
# ==========================================================================
@router.post("/{project_id}/reports", status_code=201)
def create_report(
    project_id: int, payload: ReportIn, user: CurrentUser, db: DbSession
) -> dict:
    project = _get_project(db, user, project_id)
    customer = db.get(Customer, payload.end_customer_id)
    if customer is None:
        raise NotFound("终端客户不存在")

    record = report_svc.create_report(
        db, user=user, project=project, end_customer=customer
    )
    db.commit()
    db.refresh(record)
    return report_out(record)


@router.get("/{project_id}/reports")
def list_reports(project_id: int, user: CurrentUser, db: DbSession) -> dict:
    from app.models import CustomerReport

    project = _get_project(db, user, project_id)
    rows = (
        db.execute(
            select(CustomerReport).where(CustomerReport.project_id == project.id)
        )
        .scalars()
        .all()
    )
    return {"items": [report_out(r) for r in rows]}


# ==========================================================================
# 内部
# ==========================================================================
def _maybe_lock_report(db, project: SalesProject) -> None:
    """项目进入锁定阶段后，把对应的终端客户报备转为锁定（需求文档 4.7）。"""
    from app.constants import REPORT_LOCK_STAGE, STAGE_ORDER

    if STAGE_ORDER.index(ProjectStage(project.stage)) < STAGE_ORDER.index(REPORT_LOCK_STAGE):
        return
    if project.end_customer_id is None:
        return

    active = report_svc.get_active_report(db, project.end_customer_id)
    if active is not None and active.project_id == project.id:
        report_svc.lock_report(db, report=active, project=project)
