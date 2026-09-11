"""销售项目业务规则与流程编排。

需求文档 4.8 节 R-20~R-30。

关键设计：阶段推进是**唯一**改变 ``stage`` 的入口。任何绕过 ``advance_stage``
直改字段的写法都会跳过门禁与历史留痕，因此模型层不暴露 setter。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import (
    PRODUCT_SERIES_MODELS,
    PROJECT_CATEGORY_LABELS,
    STAGE_LABELS,
    STAGE_TYPICAL_DAYS,
    STAGE_WIN_RATE,
    ChannelType,
    CloseType,
    ProjectCategory,
    ProjectStage,
    ProjectStatus,
    StageAction,
)
from app.models import Customer, ProjectStageHistory, SalesProject
from app.models.base import utcnow
from app.services import gate, permission, sequence
from app.services.errors import NotFound, ValidationFailed


# ==========================================================================
# 纯计算函数
# ==========================================================================
def compute_revenue(
    unit_price: Decimal | float | None,
    annual_qty: int | None,
    lifecycle_years: int | None,
) -> tuple[Decimal | None, Decimal | None]:
    """双维度金额口径（需求文档 2.2 偏离三）。

    年营收 = 单价 × 年用量；总营收 = 年营收 × 生命周期年数。
    任一入参缺失则返回 ``(None, None)``，不做部分计算——半算出来的金额
    比没有金额更危险，会被误当成真实预测。
    """
    if unit_price is None or annual_qty is None:
        return None, None

    annual = (Decimal(str(unit_price)) * Decimal(annual_qty)).quantize(Decimal("0.01"))
    if lifecycle_years is None:
        return annual, None

    total = (annual * Decimal(lifecycle_years)).quantize(Decimal("0.01"))
    return annual, total


def check_amount_change(
    old_total: Decimal | float | None,
    new_total: Decimal | float | None,
    threshold: float,
) -> tuple[bool, float]:
    """R-26：金额变更幅度是否超过阈值。返回 ``(是否超阈值, 变化比例)``。

    旧值为空视为首次录入，不算变更。
    """
    if old_total is None or new_total is None:
        return False, 0.0
    old_dec = Decimal(str(old_total))
    if old_dec == 0:
        return False, 0.0

    ratio = abs(float((Decimal(str(new_total)) - old_dec) / old_dec))
    return ratio > threshold, ratio


def evaluate_stay_days(stage_enter_time: datetime, now: datetime) -> int:
    """阶段停留天数（自然日）。"""
    return max(0, (now - stage_enter_time).days)


def check_project_category(value: int | None) -> None:
    """R-31：项目类别必须是已知类别（Issue #5）。

    空值放行 —— 该字段允许不填，页面下拉里也提供了「请选择」以外的默认项。
    """
    if value is None:
        return
    if value not in {int(member) for member in ProjectCategory}:
        allowed = "、".join(PROJECT_CATEGORY_LABELS.values())
        raise ValidationFailed("R-31", f"未知的项目类别：{value}（可选：{allowed}）")


def check_product_selection(product_series: str | None, product_model: str | None) -> None:
    """R-31：产品型号必须隶属所选产品系列（Issue #5）。

    页面上做了一版联动下拉，但前端过滤只是体验，**服务端必须再判一次** ——
    直接调接口、或改 DOM 都能绕过前端。这是典型的「只在界面成立」的约束，
    一旦漏判，库里就会沉淀出「卫星通信天线 + YECT005W1A」这类坏组合。
    """
    series = (product_series or "").strip()
    model = (product_model or "").strip()

    if not series:
        if model:
            raise ValidationFailed("R-31", "请先选择产品系列，再选择产品型号")
        return

    models = PRODUCT_SERIES_MODELS.get(series)
    if models is None:
        allowed = "、".join(PRODUCT_SERIES_MODELS)
        raise ValidationFailed("R-31", f"未知的产品系列：{series}（可选：{allowed}）")

    if model and model not in models:
        raise ValidationFailed(
            "R-31",
            f"产品型号 {model} 不属于产品系列「{series}」",
            details={"product_series": series, "allowed_models": list(models)},
        )


# ==========================================================================
# 查询
# ==========================================================================
def get_project(db: Session, project_id: int) -> SalesProject:
    project = db.get(SalesProject, project_id)
    if project is None:
        raise NotFound("销售项目不存在")
    return project


def assert_can_operate(db: Session, user, project: SalesProject) -> permission.DataScope:
    """常规操作（推进/回退/改产出物）的权限闸门。"""
    scope = permission.resolve_scope(db, user)
    decision = permission.decide_read(user, project.owner_id, scope)
    decision.raise_if_denied()
    return scope


# ==========================================================================
# 创建
# ==========================================================================
def create_project(
    db: Session,
    *,
    user,
    project_name: str,
    customer: Customer,
    channel_type: int = ChannelType.DIRECT,
    project_type: int = 1,
    product_lines: list[str] | None = None,
    applied_industry: str | None = None,
    project_category: int | None = None,
    product_series: str | None = None,
    product_model: str | None = None,
    expected_dwin_date: date | None = None,
    unit_price: Decimal | float | None = None,
    est_annual_qty: int | None = None,
    lifecycle_years: int | None = None,
    currency: str = "CNY",
    competitor: str | None = None,
    expected_sign_date: date | None = None,
    expected_mp_date: date | None = None,
    end_customer_id: int | None = None,
    stage: ProjectStage = ProjectStage.OPPORTUNITY,
) -> SalesProject:
    """创建销售项目，并预生成全部阶段的产出物清单。"""
    if not permission.can_create(user):
        raise ValidationFailed("PERM-403", "当前角色不允许新建销售项目")

    # R-31：选项类字段的取值约束（Issue #5）
    check_project_category(project_category)
    check_product_selection(product_series, product_model)

    now = utcnow()
    annual, total = compute_revenue(unit_price, est_annual_qty, lifecycle_years)

    project = SalesProject(
        project_no=sequence.next_project_no(db, now),
        project_name=project_name,
        customer_id=customer.id,
        end_customer_id=end_customer_id,
        owner_id=user.id,
        team_id=user.team_id,
        channel_type=channel_type,
        project_type=project_type,
        product_lines=product_lines,
        applied_industry=applied_industry,
        project_category=project_category,
        product_series=product_series or None,
        product_model=product_model or None,
        expected_dwin_date=expected_dwin_date,
        stage=stage.value,
        stage_enter_time=now,
        stage_stay_days=0,
        stage_stagnant=False,
        win_rate=STAGE_WIN_RATE[stage],
        unit_price=unit_price,
        est_annual_qty=est_annual_qty,
        lifecycle_years=lifecycle_years,
        est_annual_revenue=annual,
        est_total_revenue=total,
        currency=currency,
        competitor=competitor,
        expected_sign_date=expected_sign_date,
        expected_mp_date=expected_mp_date,
        status=ProjectStatus.ONGOING,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(project)
    db.flush()

    gate.provision_items(db, project)

    # 初始阶段历史：便于后续统一按历史表做停留分析
    db.add(
        ProjectStageHistory(
            project_id=project.id,
            from_stage=None,
            to_stage=stage.value,
            action=StageAction.ADVANCE,
            stay_days=0,
            gate_passed=True,
            operator_id=user.id,
            operated_at=now,
            remark="项目创建",
        )
    )
    db.flush()
    return project


# ==========================================================================
# 阶段推进 / 回退
# ==========================================================================
def advance_stage(
    db: Session,
    *,
    user,
    project: SalesProject,
    override_reason: str | None = None,
    remark: str | None = None,
    visit_triggered: bool = False,
) -> SalesProject:
    """推进到下一阶段。

    门禁规则（R-20 / R-21）：
      - 当前阶段必需产出物齐备 → 直接推进
      - 不齐备且未提供覆盖理由 → 阻断，列出缺失项
      - 不齐备但提供了合规的覆盖理由且操作人有权限 → 放行并留痕
    """
    assert_can_operate(db, user, project)

    if project.is_closed:
        raise ValidationFailed("R-25", "项目已关闭，不可再推进阶段")

    current = project.stage_enum
    target = gate.next_stage(current)
    if target is None:
        raise ValidationFailed("STAGE-01", "已处于最后阶段，无法继续推进")

    # R-20
    check = gate.check_gate(db, project, current)
    override_used = False

    if not check.passed:
        if not override_reason or not override_reason.strip():
            raise ValidationFailed(
                "R-20",
                check.summary(),
                details={"missing": check.missing, "stage": current.value},
            )

        # R-21：先验权限再验理由长度。反过来会把「理由格式」这种内部规则
        # 泄露给本就没有覆盖权限的角色。
        scope = permission.resolve_scope(db, user)
        decision = permission.decide_gate_override(user, scope)
        decision.raise_if_denied()

        if len(override_reason.strip()) < settings.override_reason_min_chars:
            raise ValidationFailed(
                "R-21",
                f"门禁覆盖理由至少 {settings.override_reason_min_chars} 字",
                details={"actual": len(override_reason.strip())},
            )

        override_used = True

    now = utcnow()
    stay_days = evaluate_stay_days(project.stage_enter_time, now)

    project.stage = target.value
    project.stage_enter_time = now
    project.stage_stay_days = 0
    project.stage_stagnant = False
    project.win_rate = STAGE_WIN_RATE[target]
    project.updated_by = user.id

    db.add(
        ProjectStageHistory(
            project_id=project.id,
            from_stage=current.value,
            to_stage=target.value,
            action=StageAction.ADVANCE,
            stay_days=stay_days,
            gate_passed=not override_used,
            override_by=user.id if override_used else None,
            override_reason=override_reason.strip() if override_used else None,
            operator_id=user.id,
            operated_at=now,
            remark=remark,
        )
    )

    # 推进到终态阶段等价于赢单关闭
    if target is ProjectStage.WON:
        project.status = ProjectStatus.CLOSED
        project.close_type = CloseType.WON
        project.close_reason = remark or "推进至赢单阶段"

    db.flush()
    return project


def rollback_stage(
    db: Session,
    *,
    user,
    project: SalesProject,
    reason: str,
    remark: str | None = None,
) -> SalesProject:
    """回退到上一阶段（R-22）。"""
    assert_can_operate(db, user, project)

    if project.is_closed:
        raise ValidationFailed("R-25", "项目已关闭，不可回退阶段")

    if not reason or len(reason.strip()) < settings.rollback_reason_min_chars:
        raise ValidationFailed(
            "R-22",
            f"阶段回退原因至少 {settings.rollback_reason_min_chars} 字",
            details={"actual": len((reason or "").strip())},
        )

    current = project.stage_enum
    target = gate.prev_stage(current)
    if target is None:
        raise ValidationFailed("STAGE-02", "已处于首个阶段，无法回退")

    now = utcnow()
    stay_days = evaluate_stay_days(project.stage_enter_time, now)

    project.stage = target.value
    project.stage_enter_time = now
    project.stage_stay_days = 0
    project.stage_stagnant = False
    project.win_rate = STAGE_WIN_RATE[target]
    project.updated_by = user.id

    db.add(
        ProjectStageHistory(
            project_id=project.id,
            from_stage=current.value,
            to_stage=target.value,
            action=StageAction.ROLLBACK,
            stay_days=stay_days,
            gate_passed=True,
            operator_id=user.id,
            operated_at=now,
            remark=remark or reason.strip(),
        )
    )
    db.flush()
    return project


# ==========================================================================
# 关闭 / 重新激活
# ==========================================================================
def close_project(
    db: Session,
    *,
    user,
    project: SalesProject,
    close_type: CloseType,
    reason: str,
    lost_to_competitor: str | None = None,
) -> SalesProject:
    """关闭项目（R-23 / R-24 / R-25）。"""
    scope = permission.resolve_scope(db, user)
    decision = permission.decide_project_close(user, scope, project.owner_id)
    decision.raise_if_denied()

    if project.is_closed:
        raise ValidationFailed("R-25", "项目已关闭，不可重复关闭")

    # R-23
    if not reason or not reason.strip():
        raise ValidationFailed("R-23", "关闭原因必填")

    # R-24
    if close_type is CloseType.LOST and not (lost_to_competitor or "").strip():
        raise ValidationFailed("R-24", "输单必须填写输给哪个竞品")

    now = utcnow()
    stay_days = evaluate_stay_days(project.stage_enter_time, now)

    project.status = ProjectStatus.CLOSED
    project.close_type = close_type
    project.close_reason = reason.strip()
    project.lost_to_competitor = lost_to_competitor
    project.updated_by = user.id

    db.add(
        ProjectStageHistory(
            project_id=project.id,
            from_stage=project.stage,
            to_stage=project.stage,
            action=StageAction.CLOSE,
            stay_days=stay_days,
            gate_passed=True,
            operator_id=user.id,
            operated_at=now,
            remark=f"{close_type.name}: {reason.strip()}",
        )
    )
    db.flush()
    return project


def reactivate_project(db: Session, *, user, project: SalesProject) -> SalesProject:
    """把「搁置」状态的项目重新激活。赢单/输单是终态，不可激活。"""
    if not project.is_closed:
        raise ValidationFailed("STATE-03", "项目当前未关闭，无需激活")

    if project.close_type in (CloseType.WON, CloseType.LOST):
        raise ValidationFailed("R-25", "赢单或输单为终态，不可重新激活")

    role = user.role_enum
    if role.value not in ("admin", "sales_director", "sales_manager"):
        raise ValidationFailed("PERM-403", "仅主管及以上可重新激活项目")

    project.status = ProjectStatus.ONGOING
    project.close_type = None
    project.close_reason = None
    project.stage_enter_time = utcnow()
    project.stage_stay_days = 0
    project.stage_stagnant = False
    project.updated_by = user.id
    db.flush()
    return project


# ==========================================================================
# 更新
# ==========================================================================
def update_project(
    db: Session,
    *,
    user,
    project: SalesProject,
    approval_granted: bool = False,
    **fields,
) -> SalesProject:
    """更新项目字段。

    R-26：金额变更超过阈值需审批（``approval_granted=True`` 表示已获批准）。
    R-30：终端客户锁定后，终端客户相关字段不可修改。
    """
    assert_can_operate(db, user, project)

    locked_fields = {"end_customer_id", "end_customer_locked"}
    if project.end_customer_locked and locked_fields & set(fields):
        raise ValidationFailed(
            "R-30", "终端客户已锁定，不可修改终端客户字段"
        )

    # 重新计算派生金额
    new_price = fields.get("unit_price", project.unit_price)
    new_qty = fields.get("est_annual_qty", project.est_annual_qty)
    new_life = fields.get("lifecycle_years", project.lifecycle_years)
    annual, total = compute_revenue(new_price, new_qty, new_life)

    # R-26
    exceeded, ratio = check_amount_change(
        project.est_total_revenue, total, settings.amount_change_threshold
    )
    if exceeded and not approval_granted:
        raise ValidationFailed(
            "R-26",
            f"项目金额变更 {ratio:.1%} 超过阈值 "
            f"{settings.amount_change_threshold:.0%}，需主管审批",
            details={"change_ratio": round(ratio, 4)},
        )

    for key, value in fields.items():
        if hasattr(project, key):
            setattr(project, key, value)

    project.est_annual_revenue = annual
    project.est_total_revenue = total
    project.updated_by = user.id
    db.flush()
    return project


# ==========================================================================
# 删除
# ==========================================================================
def delete_project(db: Session, *, user, project: SalesProject) -> None:
    """R-29：仅创建后 24 小时内且无阶段推进历史可删除，否则只能关闭。"""
    now = utcnow()
    if now > project.created_at + timedelta(hours=settings.edit_window_hours):
        raise ValidationFailed("R-29", "项目创建已超过 24 小时，只能关闭不能删除")

    history_count = db.execute(
        select(func.count())
        .select_from(ProjectStageHistory)
        .where(
            ProjectStageHistory.project_id == project.id,
            ProjectStageHistory.action != StageAction.ADVANCE,
        )
    ).scalar_one()
    if history_count > 0:
        raise ValidationFailed("R-29", "项目已有阶段流转历史，不能删除")

    db.delete(project)
    db.flush()


# ==========================================================================
# 停滞扫描（R-28）
# ==========================================================================
def refresh_stage_metrics(db: Session, now: datetime | None = None) -> int:
    """刷新所有进行中项目的阶段停留天数与停滞标记。

    应由定时任务每日执行；也用于测试中直接构造停滞场景。
    返回被标记为停滞的项目数。
    """
    moment = now or utcnow()
    projects = db.execute(
        select(SalesProject).where(SalesProject.status == ProjectStatus.ONGOING)
    ).scalars().all()

    stagnant_count = 0
    for project in projects:
        stay = evaluate_stay_days(project.stage_enter_time, moment)
        project.stage_stay_days = stay
        project.stage_stagnant = gate.evaluate_stagnant(project.stage, stay)
        if project.stage_stagnant:
            stagnant_count += 1

    db.flush()
    return stagnant_count


def stage_summary(db: Session, user) -> list[dict]:
    """各阶段项目数与金额分布（销售漏斗数据源）。"""
    scope = permission.resolve_scope(db, user)

    conditions = []
    if not scope.is_full:
        conditions.append(SalesProject.owner_id.in_(list(scope.visible_user_ids or [])))

    rows = db.execute(
        select(SalesProject).where(*conditions)
    ).scalars().all()

    buckets: dict[str, dict] = {
        stage.value: {
            "stage": stage.value,
            "label": STAGE_LABELS[stage],
            "typical_days": STAGE_TYPICAL_DAYS[stage],
            "win_rate": STAGE_WIN_RATE[stage],
            "count": 0,
            "total_revenue": Decimal("0"),
            "weighted_revenue": Decimal("0"),
            "stagnant": 0,
        }
        for stage in ProjectStage
    }

    for project in rows:
        if project.status != ProjectStatus.ONGOING:
            continue
        bucket = buckets[project.stage]
        bucket["count"] += 1
        revenue = Decimal(str(project.est_total_revenue or 0))
        bucket["total_revenue"] += revenue
        bucket["weighted_revenue"] += revenue * Decimal(project.win_rate) / Decimal(100)
        if project.stage_stagnant:
            bucket["stagnant"] += 1

    for bucket in buckets.values():
        bucket["total_revenue"] = float(bucket["total_revenue"])
        bucket["weighted_revenue"] = round(float(bucket["weighted_revenue"]), 2)

    return [buckets[stage.value] for stage in ProjectStage]


def list_projects(
    db: Session,
    *,
    user,
    stage: ProjectStage | None = None,
    owner_id: int | None = None,
    customer_id: int | None = None,
    stagnant_only: bool = False,
    ongoing_only: bool = False,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[SalesProject], int]:
    """按条件查询项目，自动套用数据权限。"""
    scope = permission.resolve_scope(db, user)

    conditions = []
    if not scope.is_full:
        conditions.append(SalesProject.owner_id.in_(list(scope.visible_user_ids or [])))

    if stage is not None:
        conditions.append(SalesProject.stage == ProjectStage(stage).value)
    if owner_id is not None:
        conditions.append(SalesProject.owner_id == owner_id)
    if customer_id is not None:
        conditions.append(SalesProject.customer_id == customer_id)
    if stagnant_only:
        conditions.append(SalesProject.stage_stagnant.is_(True))
    if ongoing_only:
        conditions.append(SalesProject.status == ProjectStatus.ONGOING)
    if keyword:
        conditions.append(SalesProject.project_name.like(f"%{keyword}%"))

    total = db.execute(
        select(func.count()).select_from(SalesProject).where(*conditions)
    ).scalar_one()

    rows = (
        db.execute(
            select(SalesProject)
            .where(*conditions)
            .order_by(SalesProject.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)
