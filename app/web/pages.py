"""页面路由（服务端渲染）。

与 ``app/api`` 的分工：API 层供移动端与集成调用，页面层供浏览器使用。
页面层**复用同一套 service 函数**，不重复实现业务规则 —— 否则两套入口
的规则必然漂移，最后没人知道以哪个为准。

表单提交一律走「POST → 303 重定向」，不用 fetch 后局部刷新：
E2E 测试驱动表单时，后者会引入大量异步时序问题，而收益只有一点点体验提升。
"""

from __future__ import annotations

import os
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.api.deps import get_optional_user, sign_session
from app.config import settings
from app.constants import (
    CHANNEL_TYPE_LABELS,
    LOCATION_STATUS_LABELS,
    PLAN_STATUS_LABELS,
    PRODUCT_SERIES_MODELS,
    PROJECT_CATEGORY_LABELS,
    PROJECT_TYPE_LABELS,
    ROLE_LABELS,
    STAGE_LABELS,
    STAGE_ORDER,
    VISIT_TYPE_LABELS,
    ChannelType,
    LocationStatus,
    PlanStatus,
    ProjectCategory,
    ProjectStage,
    ProjectStatus,
    ProjectType,
    Role,
    VisitType,
)
from app.database import SessionLocal
from app.models import Customer, SalesProject, VisitPlan, VisitRecord
from app.models.base import utcnow
from app.services import auth as auth_svc
from app.services import gate, permission
from app.services import project as project_svc
from app.services import visit as visit_svc
from app.services.errors import DomainError, ValidationFailed

router = APIRouter(tags=["pages"], include_in_schema=False)

_templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_templates_dir)

def _plain_keyed(mapping: dict) -> dict:
    """把「枚举成员为键」的字典转成「裸值为键」。

    为什么必须做这件事：模板里 ``<option value="{{ key }}">`` 渲染的是
    ``str(key)``。对于 ``class X(int, Enum)`` 这类混入枚举，``str()`` 返回的是
    ``"VisitType.ROUTINE"`` 而不是 ``2``（只有 IntEnum 才返回数值）。
    结果就是表单提交上去一个枚举名字符串，服务端 422 —— 而接口测试因为
    直接传了整数，完全发现不了。它只在真人用浏览器操作时才暴露。

    转成裸值后 ``{{ key }}`` 天然就是正确的 ``2`` / ``"opportunity"``，
    且字典用枚举成员去查依然命中（混入枚举的 hash 与裸值一致）。
    """
    return {key.value if hasattr(key, "value") else key: value for key, value in mapping.items()}


templates.env.globals.update(
    STAGE_LABELS=_plain_keyed(STAGE_LABELS),
    STAGE_ORDER=STAGE_ORDER,
    VISIT_TYPE_LABELS=_plain_keyed(VISIT_TYPE_LABELS),
    PLAN_STATUS_LABELS=_plain_keyed(PLAN_STATUS_LABELS),
    LOCATION_STATUS_LABELS=_plain_keyed(LOCATION_STATUS_LABELS),
    PROJECT_TYPE_LABELS=_plain_keyed(PROJECT_TYPE_LABELS),
    CHANNEL_TYPE_LABELS=_plain_keyed(CHANNEL_TYPE_LABELS),
    ROLE_LABELS=_plain_keyed(ROLE_LABELS),
    PROJECT_CATEGORY_LABELS=_plain_keyed(PROJECT_CATEGORY_LABELS),
    #: 产品系列 → 型号清单。模板拿它渲染联动下拉，与服务端校验同源（Issue #5）。
    PRODUCT_SERIES_MODELS=PRODUCT_SERIES_MODELS,
    ProjectStage=ProjectStage,
    ProjectStatus=ProjectStatus,
    VisitType=VisitType,
    PlanStatus=PlanStatus,
    LocationStatus=LocationStatus,
    ProjectType=ProjectType,
    ProjectCategory=ProjectCategory,
    ChannelType=ChannelType,
    Role=Role,
)


# --------------------------------------------------------------------------
# 页面通用依赖
# --------------------------------------------------------------------------
def _render(request: Request, template: str, **context):
    """统一注入当前用户与 flash 消息。"""
    return templates.TemplateResponse(
        request,
        template,
        {
            "current_user": getattr(request.state, "user", None),
            "flash": request.query_params.get("msg"),
            "flash_level": request.query_params.get("level", "ok"),
            **context,
        },
    )


def _current_user_or_redirect(request: Request, db):
    user = get_optional_user(request, db)
    if user is None:
        return None
    request.state.user = user
    return user


def _redirect(url: str, *, msg: str | None = None, level: str = "ok"):
    if msg:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}msg={msg}&level={level}"
    return RedirectResponse(url, status_code=303)


def _scope_for(user):
    """在独立会话中解析数据范围，供模板判断按钮可见性。"""
    with SessionLocal() as db:
        return permission.resolve_scope(db, user)


def _parse_int(raw: str | None) -> int | None:
    """表单里的整数（下拉框选值）。空串＝未选，返回 None。

    非法值不回退成 None —— 那会静默丢字段。这里直接报错，
    由服务层的 R-31 统一给出可读提示。
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise ValidationFailed("R-31", f"取值必须是整数，收到：{text}") from exc


def _parse_date(raw: str | None, label: str) -> date | None:
    """``<input type="date">`` 提交的 ``YYYY-MM-DD``。空串＝未填。"""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValidationFailed("R-32", f"{label}格式不正确（应为 YYYY-MM-DD）：{text}") from exc


# --------------------------------------------------------------------------
# 登录
# --------------------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return _render(request, "login.html", hide_nav=True)


@router.post("/login")
def login_submit(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    with SessionLocal() as db:
        try:
            user = auth_svc.authenticate(db, username, password)
        except DomainError:
            return _redirect("/login", msg="用户名或密码错误", level="err")

    response = _redirect("/")
    response.set_cookie(
        key=settings.session_cookie,
        value=sign_session(user.id),
        httponly=True,
        samesite="lax",
        max_age=settings.session_max_age,
        path="/",
    )
    return response


@router.get("/logout")
def logout():
    response = _redirect("/login", msg="已退出登录")
    response.delete_cookie(settings.session_cookie, path="/")
    return response


# --------------------------------------------------------------------------
# 首页看板
# --------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        funnel = project_svc.stage_summary(db, user)
        stagnant = project_svc.refresh_stage_metrics(db)

        scope = permission.resolve_scope(db, user)
        visit_conditions = [VisitRecord.is_deleted == 0]
        project_conditions = [SalesProject.status == ProjectStatus.ONGOING]
        if not scope.is_full:
            ids = list(scope.visible_user_ids or [])
            visit_conditions.append(VisitRecord.owner_id.in_(ids))
            project_conditions.append(SalesProject.owner_id.in_(ids))

        visits = db.execute(
            select(VisitRecord)
            .where(*visit_conditions)
            .order_by(VisitRecord.checkin_time.desc())
            .limit(8)
        ).scalars().all()

        abnormal_rows = db.execute(
            select(VisitRecord).where(
                *visit_conditions, VisitRecord.abnormal_flag == 1
            )
        ).scalars().all()

        ongoing_projects = db.execute(
            select(SalesProject).where(*project_conditions)
        ).scalars().all()

        metrics = {
            "visit_total": db.execute(
                select(VisitRecord).where(*visit_conditions)
            ).scalars().all().__len__(),
            "abnormal_count": len(abnormal_rows),
            "project_count": len(ongoing_projects),
            "stagnant_count": stagnant,
            "weighted_revenue": round(
                sum(o.est_total_revenue or 0 for o in ongoing_projects), 2
            ),
        }

        db.commit()

        return _render(
            request,
            "dashboard.html",
            funnel=funnel,
            metrics=metrics,
            recent_visits=visits,
            projects=ongoing_projects[:6],
        )


# --------------------------------------------------------------------------
# 客户
# --------------------------------------------------------------------------
@router.get("/customers", response_class=HTMLResponse)
def customers_page(request: Request, keyword: str | None = None):
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        scope = permission.resolve_scope(db, user)
        conditions = []
        if not scope.is_full:
            conditions.append(Customer.owner_id.in_(list(scope.visible_user_ids or [])))
        if keyword:
            conditions.append(Customer.name.like(f"%{keyword}%"))

        rows = db.execute(
            select(Customer).where(*conditions).order_by(Customer.id.desc()).limit(200)
        ).scalars().all()

        return _render(
            request,
            "customers.html",
            customers=rows,
            keyword=keyword or "",
            can_create=permission.can_create(user),
        )


@router.post("/customers")
def customer_create(
    request: Request,
    name: Annotated[str, Form()],
    industry: Annotated[str, Form()] = "",
    level: Annotated[str, Form()] = "",
    address: Annotated[str, Form()] = "",
    contact_name: Annotated[str, Form()] = "",
    contact_phone: Annotated[str, Form()] = "",
):
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        if not permission.can_create(user):
            return _redirect("/customers", msg="当前角色不允许新建客户", level="err")

        customer = Customer(
            name=name,
            industry=industry or None,
            level=level or None,
            address=address or None,
            contact_name=contact_name or None,
            contact_phone=contact_phone or None,
            owner_id=user.id,
            team_id=user.team_id,
            created_by=user.id,
        )
        db.add(customer)
        db.commit()
        return _redirect(f"/customers/{customer.id}", msg=f"客户【{name}】创建成功")


@router.get("/customers/{customer_id}", response_class=HTMLResponse)
def customer_detail(request: Request, customer_id: int):
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        customer = db.get(Customer, customer_id)
        if customer is None:
            return _redirect("/customers", msg="客户不存在", level="err")

        scope = permission.resolve_scope(db, user)
        decision = permission.decide_read(user, customer.owner_id, scope)
        if not decision.allowed:
            return _redirect("/customers", msg="无权查看该客户", level="err")

        visit_conditions = [
            VisitRecord.customer_id == customer.id,
            VisitRecord.is_deleted == 0,
        ]
        project_conditions = [SalesProject.customer_id == customer.id]
        if not scope.is_full:
            ids = list(scope.visible_user_ids or [])
            visit_conditions.append(VisitRecord.owner_id.in_(ids))
            project_conditions.append(SalesProject.owner_id.in_(ids))

        visits = db.execute(
            select(VisitRecord)
            .where(*visit_conditions)
            .order_by(VisitRecord.checkin_time.desc())
        ).scalars().all()
        projects = db.execute(
            select(SalesProject).where(*project_conditions)
        ).scalars().all()

        return _render(
            request,
            "customer_detail.html",
            customer=customer,
            visits=visits,
            projects=projects,
            has_coords=customer.latitude is not None and customer.longitude is not None,
        )


# --------------------------------------------------------------------------
# 拜访
# --------------------------------------------------------------------------
@router.get("/visits", response_class=HTMLResponse)
def visits_page(
    request: Request,
    abnormal_only: int = 0,
    customer_id: int | None = None,
    customer_keyword: str = "",
):
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        rows, total = visit_svc.list_records(
            db,
            user=user,
            customer_id=customer_id,
            customer_keyword=customer_keyword,
            abnormal_only=bool(abnormal_only),
            limit=200,
        )
        scope = permission.resolve_scope(db, user)
        customers = db.execute(
            select(Customer)
            .where(
                *(
                    []
                    if scope.is_full
                    else [Customer.owner_id.in_(list(scope.visible_user_ids or []))]
                )
            )
            .order_by(Customer.name)
        ).scalars().all()

        return _render(
            request,
            "visits.html",
            visits=rows,
            total=total,
            abnormal_only=bool(abnormal_only),
            customer_id=customer_id,
            customer_keyword=customer_keyword,
            customers=customers,
        )


@router.get("/visits/new", response_class=HTMLResponse)
def visit_new(request: Request, customer_id: int | None = None):
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        scope = permission.resolve_scope(db, user)
        customers = db.execute(
            select(Customer)
            .where(
                *(
                    []
                    if scope.is_full
                    else [Customer.owner_id.in_(list(scope.visible_user_ids or []))]
                )
            )
            .order_by(Customer.name)
        ).scalars().all()

        selected = db.get(Customer, customer_id) if customer_id else None
        plans = db.execute(
            select(VisitPlan)
            .where(VisitPlan.status == PlanStatus.PENDING)
            .order_by(VisitPlan.plan_start)
        ).scalars().all()

        return _render(
            request,
            "visit_new.html",
            customers=customers,
            selected=selected,
            plans=plans,
        )


@router.post("/visits/checkin")
def visit_checkin(
    request: Request,
    customer_id: Annotated[int, Form()],
    address: Annotated[str, Form()] = "",
    longitude: Annotated[str, Form()] = "",
    latitude: Annotated[str, Form()] = "",
    visit_type: Annotated[int, Form()] = 2,
    is_mocked: Annotated[int, Form()] = 0,
    plan_id: Annotated[str, Form()] = "",
):
    """页面签到。

    坐标为空的场景（浏览器拒绝定位 / 测试环境无 GPS）默认回落到客户登记坐标，
    这样流程不会被环境卡死；真实 App 端必须拿到实时坐标，此处的兜底仅用于
    Web 端与自动化测试。
    """
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        customer = db.get(Customer, customer_id)
        if customer is None:
            return _redirect("/visits/new", msg="客户不存在", level="err")

        lng = float(longitude) if longitude else customer.longitude
        lat = float(latitude) if latitude else customer.latitude
        if lng is None or lat is None:
            return _redirect(
                "/visits/new",
                msg="客户未登记坐标，请先补全客户地址或在表单中手动填写坐标",
                level="err",
            )

        plan = db.get(VisitPlan, int(plan_id)) if plan_id else None

        try:
            record = visit_svc.checkin(
                db,
                user=user,
                customer=customer,
                plan=plan,
                lng=float(lng),
                lat=float(lat),
                address=address or customer.address or "未填写地址",
                is_mocked=bool(is_mocked),
                visit_type=visit_type,
            )
        except DomainError as exc:
            db.rollback()
            return _redirect("/visits/new", msg=f"[{exc.code}] {exc.message}", level="err")

        db.commit()
        return _redirect(
            f"/visits/{record.id}",
            msg=f"签到成功，定位状态：{LOCATION_STATUS_LABELS.get(LocationStatus(record.location_status))}",
        )


@router.get("/visits/{record_id}", response_class=HTMLResponse)
def visit_detail(request: Request, record_id: int):
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        record = db.get(VisitRecord, record_id)
        if record is None or record.is_deleted:
            return _redirect("/visits", msg="拜访记录不存在", level="err")

        scope = permission.resolve_scope(db, user)
        if not permission.decide_read(user, record.owner_id, scope).allowed:
            return _redirect("/visits", msg="无权查看该拜访记录", level="err")

        return _render(request, "visit_detail.html", record=record)


@router.post("/visits/{record_id}/checkout")
def visit_checkout(
    request: Request,
    record_id: int,
    content: Annotated[str, Form()] = "",
    customer_feedback: Annotated[str, Form()] = "",
    next_action: Annotated[str, Form()] = "",
    location_note: Annotated[str, Form()] = "",
):
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        record = db.get(VisitRecord, record_id)
        if record is None:
            return _redirect("/visits", msg="拜访记录不存在", level="err")

        try:
            visit_svc.checkout(
                db,
                user=user,
                record=record,
                content=content,
                customer_feedback=customer_feedback or None,
                next_action=next_action or None,
                location_note=location_note or None,
            )
        except DomainError as exc:
            db.rollback()
            return _redirect(f"/visits/{record_id}", msg=f"[{exc.code}] {exc.message}", level="err")

        db.commit()
        level = "warn" if record.abnormal_flag else "ok"
        note = f"拜访已提交（时长 {record.duration_min} 分钟）"
        if record.abnormal_flag:
            note += f"，已标记异常：{record.abnormal_reason}"
        return _redirect(f"/visits/{record_id}", msg=note, level=level)


# --------------------------------------------------------------------------
# 销售项目
# --------------------------------------------------------------------------
@router.get("/projects", response_class=HTMLResponse)
def projects_page(
    request: Request,
    stage: str | None = None,
    stagnant_only: int = 0,
):
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        stage_enum = ProjectStage(stage) if stage else None
        rows, total = project_svc.list_projects(
            db,
            user=user,
            stage=stage_enum,
            stagnant_only=bool(stagnant_only),
            ongoing_only=not stage_enum,
            limit=200,
        )

        scope = permission.resolve_scope(db, user)
        customers = db.execute(
            select(Customer)
            .where(
                *(
                    []
                    if scope.is_full
                    else [Customer.owner_id.in_(list(scope.visible_user_ids or []))]
                )
            )
            .order_by(Customer.name)
        ).scalars().all()

        return _render(
            request,
            "projects.html",
            projects=rows,
            total=total,
            customers=customers,
            active_stage=stage,
            stagnant_only=bool(stagnant_only),
        )


@router.post("/projects")
def project_create(
    request: Request,
    project_name: Annotated[str, Form()],
    customer_id: Annotated[int, Form()],
    unit_price: Annotated[str, Form()] = "",
    est_annual_qty: Annotated[str, Form()] = "",
    lifecycle_years: Annotated[str, Form()] = "",
    project_type: Annotated[int, Form()] = 1,
    channel_type: Annotated[int, Form()] = 1,
    applied_industry: Annotated[str, Form()] = "",
    competitor: Annotated[str, Form()] = "",
    # Issue #5 新增栏位。名字必须与模板 name 一致 —— 不一致时 FastAPI 只取默认值、
    # 不报错，字段会永远为空（见 crm-code-guard 3.1）。
    project_category: Annotated[str, Form()] = "",
    product_series: Annotated[str, Form()] = "",
    product_model: Annotated[str, Form()] = "",
    expected_dwin_date: Annotated[str, Form()] = "",
):
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        customer = db.get(Customer, customer_id)
        if customer is None:
            return _redirect("/projects", msg="客户不存在", level="err")

        try:
            project = project_svc.create_project(
                db,
                user=user,
                project_name=project_name,
                customer=customer,
                unit_price=float(unit_price) if unit_price else None,
                est_annual_qty=int(est_annual_qty) if est_annual_qty else None,
                lifecycle_years=int(lifecycle_years) if lifecycle_years else None,
                project_type=project_type,
                channel_type=channel_type,
                applied_industry=applied_industry or None,
                competitor=competitor or None,
                project_category=_parse_int(project_category),
                product_series=product_series.strip() or None,
                product_model=product_model.strip() or None,
                expected_dwin_date=_parse_date(expected_dwin_date, "预计DWIN日期"),
            )
        except DomainError as exc:
            db.rollback()
            return _redirect("/projects", msg=f"[{exc.code}] {exc.message}", level="err")

        db.commit()
        return _redirect(
            f"/projects/{project.id}", msg=f"项目【{project_name}】创建成功"
        )


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(request: Request, project_id: int):
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        project = db.get(SalesProject, project_id)
        if project is None:
            return _redirect("/projects", msg="项目不存在", level="err")

        scope = permission.resolve_scope(db, user)
        if not permission.decide_read(user, project.owner_id, scope).allowed:
            return _redirect("/projects", msg="无权查看该项目", level="err")

        gate_result = gate.check_gate(db, project)
        current_stage = project.stage_enum
        stage_items = gate.get_items(db, project.id, current_stage)

        visits = db.execute(
            select(VisitRecord)
            .where(VisitRecord.project_id == project.id, VisitRecord.is_deleted == 0)
            .order_by(VisitRecord.checkin_time.desc())
        ).scalars().all()

        history = sorted(project.stage_history, key=lambda h: h.operated_at, reverse=True)

        return _render(
            request,
            "project_detail.html",
            project=project,
            gate_result=gate_result,
            stage_items=stage_items,
            all_gate_items=gate.get_items(db, project.id),
            visits=visits,
            history=history,
            is_last=gate.is_last_stage(current_stage),
            can_override=permission.decide_gate_override(
                user, scope
            ).allowed,
        )


@router.post("/projects/{project_id}/gate/{item_code}")
def project_submit_gate(
    request: Request,
    project_id: int,
    item_code: str,
    content: Annotated[str, Form()] = "",
):
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        project = db.get(SalesProject, project_id)
        if project is None:
            return _redirect("/projects", msg="项目不存在", level="err")

        try:
            gate.mark_item_submitted(
                db,
                project_id=project.id,
                item_code=item_code,
                operator_id=user.id,
                content=content or None,
            )
        except DomainError as exc:
            db.rollback()
            return _redirect(
                f"/projects/{project_id}", msg=f"[{exc.code}] {exc.message}", level="err"
            )

        db.commit()
        return _redirect(f"/projects/{project_id}", msg=f"产出物 {item_code} 已提交")


@router.post("/projects/{project_id}/advance")
def project_advance(
    request: Request,
    project_id: int,
    remark: Annotated[str, Form()] = "",
    override_reason: Annotated[str, Form()] = "",
):
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        project = db.get(SalesProject, project_id)
        if project is None:
            return _redirect("/projects", msg="项目不存在", level="err")

        try:
            project_svc.advance_stage(
                db,
                user=user,
                project=project,
                override_reason=override_reason or None,
                remark=remark or None,
            )
        except DomainError as exc:
            db.rollback()
            return _redirect(
                f"/projects/{project_id}", msg=f"[{exc.code}] {exc.message}", level="err"
            )

        db.commit()
        return _redirect(
            f"/projects/{project_id}",
            msg=f"已推进至【{STAGE_LABELS.get(project.stage_enum)}】阶段，赢率 {project.win_rate}%",
        )


@router.post("/projects/{project_id}/rollback")
def project_rollback(
    request: Request,
    project_id: int,
    reason: Annotated[str, Form()] = "",
):
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        project = db.get(SalesProject, project_id)
        if project is None:
            return _redirect("/projects", msg="项目不存在", level="err")

        try:
            project_svc.rollback_stage(db, user=user, project=project, reason=reason)
        except DomainError as exc:
            db.rollback()
            return _redirect(
                f"/projects/{project_id}", msg=f"[{exc.code}] {exc.message}", level="err"
            )

        db.commit()
        return _redirect(
            f"/projects/{project_id}",
            msg=f"已回退至【{STAGE_LABELS.get(project.stage_enum)}】阶段",
        )


@router.post("/projects/{project_id}/close")
def project_close(
    request: Request,
    project_id: int,
    close_type: Annotated[int, Form()],
    # 参数名必须与模板里的 name="close_reason" 一致。
    # 曾经写成 reason，而 FastAPI 对缺失的 Form 字段只取默认值不报错 ——
    # 结果是「关闭原因」永远收不到，却抛出 R-23「关闭原因必填」，
    # 把排查方向直接引到错误的地方。
    close_reason: Annotated[str, Form()] = "",
    lost_to_competitor: Annotated[str, Form()] = "",
):
    from app.constants import CloseType

    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        project = db.get(SalesProject, project_id)
        if project is None:
            return _redirect("/projects", msg="项目不存在", level="err")

        try:
            project_svc.close_project(
                db,
                user=user,
                project=project,
                close_type=CloseType(close_type),
                reason=close_reason,
                lost_to_competitor=lost_to_competitor or None,
            )
        except DomainError as exc:
            db.rollback()
            return _redirect(
                f"/projects/{project_id}", msg=f"[{exc.code}] {exc.message}", level="err"
            )

        db.commit()
        return _redirect(f"/projects/{project_id}", msg="项目已关闭")


@router.get("/funnel", response_class=HTMLResponse)
def funnel_page(request: Request):
    with SessionLocal() as db:
        user = _current_user_or_redirect(request, db)
        if user is None:
            return _redirect("/login")

        project_svc.refresh_stage_metrics(db)
        db.commit()
        rows = project_svc.stage_summary(db, user)
        return _render(request, "funnel.html", stages=rows, generated_at=utcnow())
