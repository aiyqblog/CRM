"""数据权限。

需求文档 3.2 节权限矩阵的可执行版本。核心是「行级控制」：不是简单判断角色，
而是先把当前用户能看见的人解析成一个集合，再判断目标数据的 owner 是否落在集合内。

越权防护是接口测试的重点，见 tests/api/test_permissions.py。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import Role
from app.models import Team, User
from app.services.errors import PermissionDenied


@dataclass(frozen=True)
class DataScope:
    """一个用户的数据可见范围。

    ``visible_user_ids`` 为 ``None`` 表示全量可见（总监/管理员/市场）。
    """

    user_id: int
    role: Role
    team_ids: frozenset[int] = field(default_factory=frozenset)
    visible_user_ids: frozenset[int] | None = None

    @property
    def is_full(self) -> bool:
        return self.visible_user_ids is None

    def covers(self, owner_id: int | None) -> bool:
        if owner_id is None:
            return False
        if self.visible_user_ids is None:
            return True
        return owner_id in self.visible_user_ids


@dataclass(frozen=True)
class AccessDecision:
    """一次权限判定的结果。

    ``requires_reason`` 用于表达「允许，但必须留痕」这一档权限——
    需求文档里主管改下属记录需要填原因，就是这个。
    """

    allowed: bool
    requires_reason: bool = False
    code: str | None = None
    message: str = ""

    def raise_if_denied(self) -> None:
        if not self.allowed:
            raise PermissionDenied(self.message or "无权访问该数据", self.code or "PERM-403")

    def __bool__(self) -> bool:  # 便于测试里直接 assert decision
        return self.allowed


# --------------------------------------------------------------------------
# 范围解析
# --------------------------------------------------------------------------
def collect_team_ids(db: Session, root_team_id: int | None) -> set[int]:
    """自顶向下收集团队及其所有子孙团队 id。"""
    if root_team_id is None:
        return set()

    collected: set[int] = {root_team_id}
    frontier: list[int] = [root_team_id]

    while frontier:
        rows = (
            db.execute(select(Team.id).where(Team.parent_id.in_(frontier)))
            .scalars()
            .all()
        )
        new_ids = [int(r) for r in rows if int(r) not in collected]
        collected.update(new_ids)
        frontier = new_ids

    return collected


def _users_in_teams(db: Session, team_ids: set[int]) -> set[int]:
    if not team_ids:
        return set()
    rows = db.execute(select(User.id).where(User.team_id.in_(team_ids))).scalars().all()
    return {int(r) for r in rows}


def resolve_scope(db: Session, user: User) -> DataScope:
    """根据角色解析数据可见范围。"""
    role = user.role_enum

    if role in (Role.ADMIN, Role.SALES_DIRECTOR, Role.MARKETING):
        return DataScope(user_id=user.id, role=role, visible_user_ids=None)

    if role is Role.SALES_MANAGER:
        team_ids = collect_team_ids(db, user.team_id)
        visible = _users_in_teams(db, team_ids)
        visible.add(user.id)
        return DataScope(
            user_id=user.id,
            role=role,
            team_ids=frozenset(team_ids),
            visible_user_ids=frozenset(visible),
        )

    # 销售与技术支持：默认只能看自己的。被邀请参与的记录由各模块单独放行。
    return DataScope(
        user_id=user.id,
        role=role,
        visible_user_ids=frozenset({user.id}),
    )


# --------------------------------------------------------------------------
# 判定
# --------------------------------------------------------------------------
def decide_read(user: User, owner_id: int | None, scope: DataScope) -> AccessDecision:
    """是否可读。"""
    if owner_id is not None and owner_id == user.id:
        return AccessDecision(True)
    if scope.covers(owner_id):
        return AccessDecision(True)
    return AccessDecision(False, code="PERM-403", message="无权查看他人负责的数据")


def can_create(user: User) -> bool:
    """市场/产品角色为只读，不允许新建业务数据。"""
    return user.role_enum is not Role.MARKETING


def decide_visit_edit(
    user: User,
    *,
    owner_id: int,
    checkin_time: datetime,
    now: datetime,
    scope: DataScope,
    window_hours: int,
) -> AccessDecision:
    """拜访记录编辑判定（R-12 + 权限矩阵）。

    - 本人：签到后 ``window_hours`` 小时内可直接编辑
    - 主管：可改本团队，但必须填写原因
    - 总监：不可编辑
    - 管理员：可编辑
    """
    role = user.role_enum

    if role is Role.ADMIN:
        return AccessDecision(True)

    if owner_id == user.id:
        if now <= checkin_time + timedelta(hours=window_hours):
            return AccessDecision(True)
        return AccessDecision(
            False,
            code="R-12",
            message=f"已超过本人可编辑窗口（{window_hours} 小时），请走主管流程",
        )

    if role is Role.SALES_MANAGER and scope.covers(owner_id):
        return AccessDecision(True, requires_reason=True)

    if role is Role.SALES_DIRECTOR:
        return AccessDecision(False, code="PERM-403", message="销售总监不可编辑拜访记录")

    return AccessDecision(False, code="PERM-403", message="无权编辑该拜访记录")


def decide_visit_delete(
    user: User,
    *,
    owner_id: int,
    checkin_time: datetime,
    now: datetime,
    window_hours: int,
    linked_project: bool,
) -> AccessDecision:
    """拜访记录删除判定。

    R-13：已关联销售项目的拜访记录不可删除（它是阶段推进的证据链，
    删掉会让项目阶段历史失去依据）。
    """
    if linked_project:
        return AccessDecision(
            False, code="R-13", message="已关联销售项目的拜访记录不可删除"
        )

    role = user.role_enum
    if role is Role.ADMIN:
        return AccessDecision(True)

    if owner_id == user.id:
        if now <= checkin_time + timedelta(hours=window_hours):
            return AccessDecision(True)
        return AccessDecision(
            False, code="R-12", message=f"已超过本人可删除窗口（{window_hours} 小时）"
        )

    return AccessDecision(False, code="PERM-403", message="仅本人可删除自己的拜访记录")


def decide_export(user: User, scope: DataScope) -> AccessDecision:
    """导出判定。总监导出需走审批，管理员直接放行。"""
    role = user.role_enum

    if role is Role.ADMIN:
        return AccessDecision(True)
    if role is Role.SALES_DIRECTOR:
        return AccessDecision(True, requires_reason=True)
    if role is Role.SALES_MANAGER:
        return AccessDecision(True)
    if role is Role.SALES:
        return AccessDecision(True)
    return AccessDecision(False, code="PERM-403", message="该角色无导出权限")


def decide_project_close(user: User, scope: DataScope, owner_id: int) -> AccessDecision:
    """项目关闭（赢单/输单/搁置）判定。"""
    role = user.role_enum
    if role is Role.ADMIN:
        return AccessDecision(True)
    if owner_id == user.id:
        return AccessDecision(True)
    if role is Role.SALES_MANAGER and scope.covers(owner_id):
        return AccessDecision(True, requires_reason=True)
    return AccessDecision(False, code="PERM-403", message="无权关闭该项目")


def decide_gate_override(user: User, scope: DataScope) -> AccessDecision:
    """门禁覆盖判定（R-21）。只有主管及以上可覆盖，且必须填理由。"""
    role = user.role_enum
    if role in (Role.ADMIN, Role.SALES_DIRECTOR):
        return AccessDecision(True, requires_reason=True)
    if role is Role.SALES_MANAGER:
        return AccessDecision(True, requires_reason=True)
    return AccessDecision(
        False, code="R-21", message="仅主管及以上可覆盖阶段门禁"
    )
