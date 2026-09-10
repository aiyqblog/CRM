"""权限判定纯函数测试（需求文档 3.2 权限矩阵）。

不依赖数据库：直接构造 DataScope 与轻量 User 对象。
数据库相关的范围解析（团队树遍历）在 tests/api/test_permissions.py 覆盖。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.constants import Role
from app.models import User
from app.services.permission import (
    DataScope,
    can_create,
    decide_export,
    decide_gate_override,
    decide_project_close,
    decide_read,
    decide_visit_delete,
    decide_visit_edit,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 10, 12, 0)
CHECKIN = datetime(2026, 9, 10, 9, 0)


def make_user(uid: int, role: Role, team_id: int | None = 1) -> User:
    """构造不落库的用户对象，专供纯函数判定使用。"""
    return User(
        id=uid,
        username=f"user{uid}",
        password_hash="x",
        display_name=f"用户{uid}",
        role=role.value,
        team_id=team_id,
    )


SALES = make_user(10, Role.SALES)
PEER = make_user(11, Role.SALES)
MANAGER = make_user(20, Role.SALES_MANAGER)
DIRECTOR = make_user(30, Role.SALES_DIRECTOR)
ADMIN = make_user(40, Role.ADMIN)
MARKETING = make_user(50, Role.MARKETING)
TECH = make_user(60, Role.TECH_SUPPORT)

SELF_SCOPE = DataScope(user_id=10, role=Role.SALES, visible_user_ids=frozenset({10}))
TEAM_SCOPE = DataScope(
    user_id=20,
    role=Role.SALES_MANAGER,
    team_ids=frozenset({1, 2}),
    visible_user_ids=frozenset({10, 11, 20}),
)
FULL_SCOPE = DataScope(user_id=30, role=Role.SALES_DIRECTOR, visible_user_ids=None)


class TestReadDecision:
    def test_sales_sees_own(self):
        assert decide_read(SALES, 10, SELF_SCOPE).allowed

    def test_sales_cannot_see_peer(self):
        """越权核心用例：销售不可查看他人数据。"""
        decision = decide_read(SALES, 11, SELF_SCOPE)
        assert not decision.allowed
        assert decision.code == "PERM-403"

    def test_manager_sees_team(self):
        assert decide_read(MANAGER, 11, TEAM_SCOPE).allowed

    def test_manager_cannot_see_outside_team(self):
        assert not decide_read(MANAGER, 99, TEAM_SCOPE).allowed

    def test_director_sees_everything(self):
        assert decide_read(DIRECTOR, 999, FULL_SCOPE).allowed

    def test_none_owner_denied(self):
        assert not decide_read(SALES, None, SELF_SCOPE).allowed

    def test_decision_truthiness(self):
        """AccessDecision 支持直接 assert，让测试读起来更自然。"""
        assert decide_read(SALES, 10, SELF_SCOPE)
        assert not decide_read(SALES, 11, SELF_SCOPE)

    def test_raise_if_denied(self):
        from app.services.errors import PermissionDenied

        with pytest.raises(PermissionDenied):
            decide_read(SALES, 11, SELF_SCOPE).raise_if_denied()

    def test_raise_if_denied_noop_when_allowed(self):
        decide_read(SALES, 10, SELF_SCOPE).raise_if_denied()


class TestCreatePermission:
    def test_sales_can_create(self):
        assert can_create(SALES)

    def test_marketing_cannot_create(self):
        """市场/产品为只读角色。"""
        assert not can_create(MARKETING)

    def test_admin_can_create(self):
        assert can_create(ADMIN)


class TestVisitEdit:
    """R-12 + 权限矩阵：本人 24h 内可直接改，主管改本团队需填原因。"""

    def test_owner_within_window(self):
        decision = decide_visit_edit(
            SALES, owner_id=10, checkin_time=CHECKIN, now=NOW,
            scope=SELF_SCOPE, window_hours=24,
        )
        assert decision.allowed and not decision.requires_reason

    def test_owner_exactly_at_window_boundary(self):
        decision = decide_visit_edit(
            SALES, owner_id=10, checkin_time=CHECKIN,
            now=CHECKIN + timedelta(hours=24), scope=SELF_SCOPE, window_hours=24,
        )
        assert decision.allowed

    def test_owner_beyond_window_denied(self):
        decision = decide_visit_edit(
            SALES, owner_id=10, checkin_time=CHECKIN,
            now=CHECKIN + timedelta(hours=25), scope=SELF_SCOPE, window_hours=24,
        )
        assert not decision.allowed
        assert decision.code == "R-12"

    def test_manager_editing_team_member_requires_reason(self):
        decision = decide_visit_edit(
            MANAGER, owner_id=10, checkin_time=CHECKIN, now=NOW,
            scope=TEAM_SCOPE, window_hours=24,
        )
        assert decision.allowed and decision.requires_reason

    def test_manager_cannot_edit_outside_team(self):
        assert not decide_visit_edit(
            MANAGER, owner_id=99, checkin_time=CHECKIN, now=NOW,
            scope=TEAM_SCOPE, window_hours=24,
        ).allowed

    def test_director_cannot_edit(self):
        """总监只看不改 —— 避免最高层直接改动一线记录。"""
        decision = decide_visit_edit(
            DIRECTOR, owner_id=10, checkin_time=CHECKIN, now=NOW,
            scope=FULL_SCOPE, window_hours=24,
        )
        assert not decision.allowed
        assert decision.code == "PERM-403"

    def test_admin_can_always_edit(self):
        assert decide_visit_edit(
            ADMIN, owner_id=10, checkin_time=CHECKIN,
            now=CHECKIN + timedelta(days=30), scope=FULL_SCOPE, window_hours=24,
        ).allowed


class TestVisitDelete:
    """R-12 / R-13。"""

    def test_owner_within_window(self):
        assert decide_visit_delete(
            SALES, owner_id=10, checkin_time=CHECKIN, now=NOW,
            window_hours=24, linked_project=False,
        ).allowed

    def test_linked_to_project_blocked(self):
        """R-13：已关联项目的记录不可删（它是阶段推进的证据链）。"""
        decision = decide_visit_delete(
            SALES, owner_id=10, checkin_time=CHECKIN, now=NOW,
            window_hours=24, linked_project=True,
        )
        assert not decision.allowed
        assert decision.code == "R-13"

    def test_project_link_blocks_even_admin(self):
        decision = decide_visit_delete(
            ADMIN, owner_id=10, checkin_time=CHECKIN, now=NOW,
            window_hours=24, linked_project=True,
        )
        assert not decision.allowed

    def test_peer_blocked(self):
        assert not decide_visit_delete(
            SALES, owner_id=11, checkin_time=CHECKIN, now=NOW,
            window_hours=24, linked_project=False,
        ).allowed

    def test_beyond_window_blocked(self):
        decision = decide_visit_delete(
            SALES, owner_id=10, checkin_time=CHECKIN,
            now=CHECKIN + timedelta(hours=30), window_hours=24, linked_project=False,
        )
        assert not decision.allowed and decision.code == "R-12"


class TestExport:
    def test_sales_may_export_own(self):
        assert decide_export(SALES, SELF_SCOPE).allowed

    def test_director_export_requires_reason(self):
        decision = decide_export(DIRECTOR, FULL_SCOPE)
        assert decision.allowed and decision.requires_reason

    def test_marketing_cannot_export(self):
        assert not decide_export(MARKETING, FULL_SCOPE).allowed

    def test_tech_cannot_export(self):
        assert not decide_export(TECH, SELF_SCOPE).allowed


class TestProjectClose:
    def test_owner_can_close(self):
        assert decide_project_close(SALES, SELF_SCOPE, 10).allowed

    def test_manager_closing_team_project_requires_reason(self):
        decision = decide_project_close(MANAGER, TEAM_SCOPE, 10)
        assert decision.allowed and decision.requires_reason

    def test_peer_cannot_close(self):
        assert not decide_project_close(SALES, SELF_SCOPE, 11).allowed


class TestGateOverride:
    """R-21：仅主管及以上可覆盖门禁，且必须留痕。"""

    def test_sales_cannot_override(self):
        decision = decide_gate_override(SALES, SELF_SCOPE)
        assert not decision.allowed
        assert decision.code == "R-21"

    def test_tech_cannot_override(self):
        assert not decide_gate_override(TECH, SELF_SCOPE).allowed

    def test_marketing_cannot_override(self):
        assert not decide_gate_override(MARKETING, FULL_SCOPE).allowed

    def test_manager_can_override_with_reason(self):
        decision = decide_gate_override(MANAGER, TEAM_SCOPE)
        assert decision.allowed and decision.requires_reason

    def test_director_can_override_with_reason(self):
        decision = decide_gate_override(DIRECTOR, FULL_SCOPE)
        assert decision.allowed and decision.requires_reason

    def test_admin_can_override_with_reason(self):
        decision = decide_gate_override(ADMIN, FULL_SCOPE)
        assert decision.allowed and decision.requires_reason


class TestDataScope:
    def test_full_scope_covers_anyone(self):
        assert FULL_SCOPE.covers(12345)
        assert FULL_SCOPE.is_full

    def test_limited_scope(self):
        assert SELF_SCOPE.covers(10)
        assert not SELF_SCOPE.covers(11)
        assert not SELF_SCOPE.is_full

    def test_none_owner_never_covered(self):
        assert not FULL_SCOPE.covers(None)
