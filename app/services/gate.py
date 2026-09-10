"""阶段门禁（Gate）。

需求文档 4.2「阶段门禁规则」的可执行版本，对应校验规则 R-20 / R-21 / R-28。

语义约定（重要）：
    门禁校验的是**当前阶段的产出物**。即「离开机会识别阶段」的前提是
    机会识别的产出物已齐备。这与「进入某阶段需要什么」是不同的读法，
    实现时以本约定为准。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import (
    GATE_ITEM_NAMES,
    GATE_ITEMS,
    STAGE_LABELS,
    STAGE_ORDER,
    STAGE_STAGNANT_FACTOR,
    STAGE_TYPICAL_DAYS,
    GateStatus,
    ProjectStage,
)
from app.models import ProjectGateItem, SalesProject
from app.services.errors import ValidationFailed


@dataclass(frozen=True)
class GateCheckResult:
    """门禁校验结果。"""

    stage: ProjectStage
    passed: bool
    missing: list[dict] = field(default_factory=list)

    @property
    def missing_codes(self) -> list[str]:
        return [item["code"] for item in self.missing]

    def summary(self) -> str:
        if self.passed:
            return f"{STAGE_LABELS[self.stage]}阶段产出物齐备"
        names = "、".join(item["name"] for item in self.missing)
        return f"{STAGE_LABELS[self.stage]}阶段缺少必需产出物：{names}"


# --------------------------------------------------------------------------
# 阶段序运算
# --------------------------------------------------------------------------
def stage_index(stage: ProjectStage | str) -> int:
    return STAGE_ORDER.index(ProjectStage(stage))


def next_stage(stage: ProjectStage | str) -> ProjectStage | None:
    idx = stage_index(stage)
    if idx + 1 >= len(STAGE_ORDER):
        return None
    return STAGE_ORDER[idx + 1]


def prev_stage(stage: ProjectStage | str) -> ProjectStage | None:
    idx = stage_index(stage)
    if idx == 0:
        return None
    return STAGE_ORDER[idx - 1]


def is_last_stage(stage: ProjectStage | str) -> bool:
    return stage_index(stage) == len(STAGE_ORDER) - 1


# --------------------------------------------------------------------------
# 停滞判定（R-28）
# --------------------------------------------------------------------------
def evaluate_stagnant(stage: ProjectStage | str, stay_days: int) -> bool:
    """停留超过典型周期 × 1.5 即视为停滞。"""
    typical = STAGE_TYPICAL_DAYS.get(ProjectStage(stage), 0)
    if typical <= 0:
        return False
    return stay_days > typical * STAGE_STAGNANT_FACTOR


# --------------------------------------------------------------------------
# 产出物
# --------------------------------------------------------------------------
def required_item_codes(stage: ProjectStage | str) -> list[str]:
    """某阶段的必需产出物编码。"""
    return [code for code, _name, required in GATE_ITEMS.get(ProjectStage(stage), []) if required]


def provision_items(db: Session, project: SalesProject) -> list[ProjectGateItem]:
    """项目创建时按 GATE_ITEMS 预生成全部产出物清单。

    预生成而非懒加载，是为了让项目详情页能一次性展示完整检查表，
    销售能看到「后面还要交什么」，而不是推进时才发现缺东西。
    """
    created: list[ProjectGateItem] = []
    for stage, items in GATE_ITEMS.items():
        for code, name, required in items:
            item = ProjectGateItem(
                project_id=project.id,
                stage=stage.value,
                item_code=code,
                item_name=name,
                required=required,
                status=GateStatus.NOT_SUBMITTED,
            )
            db.add(item)
            created.append(item)
    db.flush()
    return created


def get_items(db: Session, project_id: int, stage: ProjectStage | str | None = None):
    stmt = select(ProjectGateItem).where(ProjectGateItem.project_id == project_id)
    if stage is not None:
        stmt = stmt.where(ProjectGateItem.stage == ProjectStage(stage).value)
    return list(
        db.execute(stmt.order_by(ProjectGateItem.id)).scalars().all()
    )


def mark_item_submitted(
    db: Session,
    *,
    project_id: int,
    item_code: str,
    operator_id: int,
    content: str | None = None,
    file_url: str | None = None,
    confirmed: bool = False,
) -> ProjectGateItem:
    """提交（或确认）某项产出物。"""
    item = db.execute(
        select(ProjectGateItem).where(
            ProjectGateItem.project_id == project_id,
            ProjectGateItem.item_code == item_code,
        )
    ).scalar_one_or_none()

    if item is None:
        raise ValidationFailed(
            "GATE-404", f"产出物 {item_code} 不存在（{GATE_ITEM_NAMES.get(item_code, '')}）"
        )

    item.content = content
    item.file_url = file_url
    item.status = GateStatus.CONFIRMED if confirmed else GateStatus.SUBMITTED
    item.submitted_by = operator_id
    item.submitted_at = datetime.utcnow()
    db.flush()
    return item


def check_gate(db: Session, project: SalesProject, stage: ProjectStage | str | None = None) -> GateCheckResult:
    """校验某阶段（默认当前阶段）的必需产出物是否齐备。"""
    target_stage = ProjectStage(stage) if stage is not None else project.stage_enum
    required_codes = required_item_codes(target_stage)

    if not required_codes:
        return GateCheckResult(stage=target_stage, passed=True, missing=[])

    items = {item.item_code: item for item in get_items(db, project.id, target_stage)}

    missing: list[dict] = []
    for code in required_codes:
        item = items.get(code)
        # 未提交(0) 视为缺失；已提交(1) 与已确认(2) 都算齐备。
        # 刻意不让「已确认」成为前置条件 —— 那会把门禁变成审批流，
        # 主管不点确认销售就卡死，失去自动化的意义。
        if item is None or item.status == GateStatus.NOT_SUBMITTED:
            missing.append(
                {
                    "code": code,
                    "name": GATE_ITEM_NAMES.get(code, code),
                }
            )

    return GateCheckResult(stage=target_stage, passed=not missing, missing=missing)
