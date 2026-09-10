"""销售项目接口测试 —— 覆盖需求文档 R-20 ~ R-30。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.constants import GATE_ITEMS, ProjectStage
from app.models import SalesProject

pytestmark = pytest.mark.api


def create_project(client, customer_id, **overrides):
    payload = {
        "project_name": "AI 模组导入项目",
        "customer_id": customer_id,
        "unit_price": 12.5,
        "est_annual_qty": 200_000,
        "lifecycle_years": 3,
    }
    payload.update(overrides)
    return client.post("/api/projects", json=payload)


def fill_gate(client, project_id, stage: ProjectStage, *, confirmed: bool = False):
    """提交某阶段的全部必需产出物。"""
    for code, _name, required in GATE_ITEMS[stage]:
        if not required:
            continue
        response = client.post(
            f"/api/projects/{project_id}/gate-items/{code}",
            json={"content": f"{code} 产出物内容", "confirmed": confirmed},
        )
        assert response.status_code == 200, f"提交 {code} 失败: {response.text}"


# ==========================================================================
# 创建与金额
# ==========================================================================
def test_create_project_computes_revenue(as_sales, customer_id):
    response = create_project(as_sales, customer_id)
    assert response.status_code == 201
    body = response.json()
    assert body["project_no"].startswith("PRJ")
    assert body["stage"] == "opportunity"
    assert body["win_rate"] == 10
    assert body["est_annual_revenue"] == 2_500_000.00
    assert body["est_total_revenue"] == 7_500_000.00


def test_create_project_provisions_all_gate_items(as_sales, customer_id):
    """创建时预生成全部 16 项产出物，让销售一开始就能看到后面要交什么。"""
    body = create_project(as_sales, customer_id).json()
    assert len(body["gate_items"]) == 16
    assert all(item["status"] == 0 for item in body["gate_items"])


def test_create_project_without_amounts(as_sales, customer_id):
    body = create_project(as_sales, customer_id, unit_price=None, est_annual_qty=None).json()
    assert body["est_annual_revenue"] is None
    assert body["est_total_revenue"] is None


def test_project_starts_with_initial_history(as_sales, customer_id):
    body = create_project(as_sales, customer_id).json()
    assert len(body["stage_history"]) == 1
    assert body["stage_history"][0]["remark"] == "项目创建"


def test_marketing_cannot_create_project(as_marketing, customer_id):
    response = create_project(as_marketing, customer_id)
    assert response.status_code == 400
    assert response.json()["code"] == "PERM-403"


# ==========================================================================
# 门禁与阶段推进
# ==========================================================================
def test_advance_blocked_when_gate_incomplete(as_sales, customer_id):
    """R-20：产出物缺失时阻断，并列出缺失项。"""
    project = create_project(as_sales, customer_id).json()
    response = as_sales.post(f"/api/projects/{project['id']}/advance", json={})

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "R-20"
    assert body["details"]["stage"] == "opportunity"
    assert "G1-01" in [item["code"] for item in body["details"]["missing"]]


def test_gate_check_endpoint(as_sales, customer_id):
    project = create_project(as_sales, customer_id).json()
    response = as_sales.get(f"/api/projects/{project['id']}/gate-check")
    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is False
    assert body["missing"]


def test_advance_succeeds_after_gate_filled(as_sales, customer_id):
    project = create_project(as_sales, customer_id).json()
    fill_gate(as_sales, project["id"], ProjectStage.OPPORTUNITY)

    response = as_sales.post(f"/api/projects/{project['id']}/advance", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["stage"] == "tech_eval"
    assert body["win_rate"] == 20


def test_sales_cannot_override_gate(as_sales, customer_id):
    """R-21：销售无覆盖权限。"""
    project = create_project(as_sales, customer_id).json()
    response = as_sales.post(
        f"/api/projects/{project['id']}/advance",
        json={"override_reason": "客户催得急，先推进再说，后面一定补上所有材料"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "R-21"


def test_manager_override_reason_too_short(as_manager, customer_id):
    """R-21：理由长度是硬门槛，主管也不例外。"""
    project = create_project(as_manager, customer_id).json()
    response = as_manager.post(
        f"/api/projects/{project['id']}/advance",
        json={"override_reason": "太短了"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "R-21"
    assert response.json()["details"]["actual"] == 3


def test_manager_can_override_with_valid_reason(as_manager, customer_id):
    project = create_project(as_manager, customer_id).json()
    reason = "客户已口头确认需求但流程尚未走完，为配合客户内部排期先行推进，材料本周内补齐留档。"

    response = as_manager.post(
        f"/api/projects/{project['id']}/advance",
        json={"override_reason": reason},
    )
    assert response.status_code == 200

    detail = as_manager.get(f"/api/projects/{project['id']}").json()
    latest = detail["stage_history"][0]
    assert latest["gate_passed"] is False
    assert latest["override_reason"] == reason
    assert latest["override_by"] is not None


def test_advance_records_stay_days(as_sales, customer_id):
    project = create_project(as_sales, customer_id).json()
    fill_gate(as_sales, project["id"], ProjectStage.OPPORTUNITY)
    as_sales.post(f"/api/projects/{project['id']}/advance", json={})

    detail = as_sales.get(f"/api/projects/{project['id']}").json()
    assert len(detail["stage_history"]) == 2
    assert detail["stage_history"][0]["from_stage"] == "opportunity"
    assert detail["stage_history"][0]["to_stage"] == "tech_eval"


def test_full_lifecycle_to_won(as_sales, customer_id):
    """走完 8 个阶段到赢单，验证阶段序与终态。"""
    project = create_project(as_sales, customer_id).json()
    pid = project["id"]

    for stage in ProjectStage:
        if stage is ProjectStage.WON:
            break
        fill_gate(as_sales, pid, stage)
        response = as_sales.post(f"/api/projects/{pid}/advance", json={})
        assert response.status_code == 200, f"{stage} 推进失败: {response.text}"

    detail = as_sales.get(f"/api/projects/{pid}").json()
    assert detail["stage"] == "won"
    assert detail["win_rate"] == 100
    assert detail["status"] == 2
    assert detail["close_type"] == 1


def test_advance_beyond_last_stage_rejected(as_sales, customer_id):
    project = create_project(as_sales, customer_id).json()
    pid = project["id"]
    for stage in ProjectStage:
        if stage is ProjectStage.WON:
            break
        fill_gate(as_sales, pid, stage)
        as_sales.post(f"/api/projects/{pid}/advance", json={})

    response = as_sales.post(f"/api/projects/{pid}/advance", json={})
    assert response.status_code == 400
    assert response.json()["code"] == "R-25"


# ==========================================================================
# 回退
# ==========================================================================
def test_rollback_requires_reason(as_sales, customer_id):
    """R-22：回退原因至少 20 字。"""
    project = create_project(as_sales, customer_id).json()
    fill_gate(as_sales, project["id"], ProjectStage.OPPORTUNITY)
    as_sales.post(f"/api/projects/{project['id']}/advance", json={})

    response = as_sales.post(
        f"/api/projects/{project['id']}/rollback", json={"reason": "太短"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "R-22"


def test_rollback_success(as_sales, customer_id):
    project = create_project(as_sales, customer_id).json()
    pid = project["id"]
    fill_gate(as_sales, pid, ProjectStage.OPPORTUNITY)
    as_sales.post(f"/api/projects/{pid}/advance", json={})

    response = as_sales.post(
        f"/api/projects/{pid}/rollback",
        json={"reason": "客户重新评估技术方案，需要退回上一阶段补充选型对比材料"},
    )
    assert response.status_code == 200
    assert response.json()["stage"] == "opportunity"
    assert response.json()["win_rate"] == 10


def test_rollback_from_first_stage_rejected(as_sales, customer_id):
    project = create_project(as_sales, customer_id).json()
    response = as_sales.post(
        f"/api/projects/{project['id']}/rollback",
        json={"reason": "已经在第一阶段了不应该能回退，这个原因长度足够二十个字"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "STAGE-02"


# ==========================================================================
# 关闭
# ==========================================================================
def test_close_requires_reason(as_sales, customer_id):
    """R-23。"""
    project = create_project(as_sales, customer_id).json()
    response = as_sales.post(
        f"/api/projects/{project['id']}/close",
        json={"close_type": 3, "reason": ""},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "R-23"


def test_close_lost_requires_competitor(as_sales, customer_id):
    """R-24：输单必须填竞品。"""
    project = create_project(as_sales, customer_id).json()
    response = as_sales.post(
        f"/api/projects/{project['id']}/close",
        json={"close_type": 2, "reason": "客户选择了其他方案"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "R-24"


def test_close_lost_with_competitor(as_sales, customer_id):
    project = create_project(as_sales, customer_id).json()
    response = as_sales.post(
        f"/api/projects/{project['id']}/close",
        json={"close_type": 2, "reason": "价格劣势", "lost_to_competitor": "竞品 X"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["close_type"] == 2
    assert body["lost_to_competitor"] == "竞品 X"


def test_close_won_is_terminal(as_sales, customer_id):
    """R-25：赢单为终态。"""
    project = create_project(as_sales, customer_id).json()
    pid = project["id"]
    fill_gate(as_sales, pid, ProjectStage.OPPORTUNITY)
    as_sales.post(f"/api/projects/{pid}/advance", json={})

    as_sales.post(
        f"/api/projects/{pid}/close", json={"close_type": 1, "reason": "客户签约"}
    )

    again = as_sales.post(
        f"/api/projects/{pid}/close", json={"close_type": 2, "reason": "反悔了"}
    )
    assert again.status_code == 400
    assert again.json()["code"] == "R-25"


def test_reactivate_shelved_project(as_manager, customer_id):
    project = create_project(as_manager, customer_id).json()
    pid = project["id"]
    as_manager.post(
        f"/api/projects/{pid}/close", json={"close_type": 3, "reason": "客户预算冻结"}
    )

    response = as_manager.post(f"/api/projects/{pid}/reactivate")
    assert response.status_code == 200
    assert response.json()["status"] == 1


def test_cannot_reactivate_lost_project(as_manager, customer_id):
    project = create_project(as_manager, customer_id).json()
    pid = project["id"]
    as_manager.post(
        f"/api/projects/{pid}/close",
        json={"close_type": 2, "reason": "客户已签竞品", "lost_to_competitor": "竞品 Y"},
    )

    response = as_manager.post(f"/api/projects/{pid}/reactivate")
    assert response.status_code == 400
    assert response.json()["code"] == "R-25"


def test_sales_cannot_reactivate(as_sales, customer_id):
    project = create_project(as_sales, customer_id).json()
    pid = project["id"]
    as_sales.post(
        f"/api/projects/{pid}/close", json={"close_type": 3, "reason": "暂缓推进"}
    )
    response = as_sales.post(f"/api/projects/{pid}/reactivate")
    assert response.status_code == 400
    assert response.json()["code"] == "PERM-403"


# ==========================================================================
# 金额变更
# ==========================================================================
def test_amount_change_within_threshold(as_sales, customer_id):
    project = create_project(as_sales, customer_id).json()
    response = as_sales.patch(
        f"/api/projects/{project['id']}",
        json={"est_annual_qty": 210_000},
    )
    assert response.status_code == 200


def test_amount_change_beyond_threshold_needs_approval(as_sales, customer_id):
    """R-26：金额变更超 20% 需审批。"""
    project = create_project(as_sales, customer_id).json()
    response = as_sales.patch(
        f"/api/projects/{project['id']}",
        json={"est_annual_qty": 500_000},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "R-26"
    assert body["details"]["change_ratio"] > 0.2


def test_amount_change_with_approval_passes(as_sales, customer_id):
    project = create_project(as_sales, customer_id).json()
    response = as_sales.patch(
        f"/api/projects/{project['id']}",
        json={"est_annual_qty": 500_000, "approval_granted": True},
    )
    assert response.status_code == 200
    assert response.json()["est_annual_qty"] == 500_000
    assert response.json()["est_annual_revenue"] == 6_250_000.00


def test_terminal_customer_locked_prevents_change(as_sales, customer_id, db):
    """R-30：报备锁定后终端客户字段不可改。"""
    project = create_project(as_sales, customer_id).json()
    row = db.get(SalesProject, project["id"])
    row.end_customer_locked = True
    db.commit()

    response = as_sales.patch(
        f"/api/projects/{project['id']}", json={"end_customer_id": 999}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "R-30"


# ==========================================================================
# 删除与停滞
# ==========================================================================
def test_delete_within_window(as_sales, customer_id):
    project = create_project(as_sales, customer_id).json()
    response = as_sales.delete(f"/api/projects/{project['id']}")
    assert response.status_code == 200


def test_delete_beyond_window_rejected(as_sales, customer_id, db):
    """R-29：超过 24 小时只能关闭不能删除。"""
    project = create_project(as_sales, customer_id).json()
    row = db.get(SalesProject, project["id"])
    row.created_at = datetime.utcnow() - timedelta(hours=30)
    db.commit()

    response = as_sales.delete(f"/api/projects/{project['id']}")
    assert response.status_code == 400
    assert response.json()["code"] == "R-29"


def test_stagnant_flag_set_by_metric_refresh(as_sales, customer_id, db):
    """R-28：停留超过典型周期 1.5 倍自动标记停滞。"""
    from app.services.project import refresh_stage_metrics

    project = create_project(as_sales, customer_id).json()
    row = db.get(SalesProject, project["id"])
    row.stage_enter_time = datetime.utcnow() - timedelta(days=30)
    db.commit()

    stagnant = refresh_stage_metrics(db)
    db.commit()
    assert stagnant >= 1

    detail = as_sales.get(f"/api/projects/{project['id']}").json()
    assert detail["stage_stagnant"] is True
    assert detail["stage_stay_days"] >= 30


def test_stagnant_filter(as_sales, customer_id, db):
    from app.services.project import refresh_stage_metrics

    project = create_project(as_sales, customer_id).json()
    row = db.get(SalesProject, project["id"])
    row.stage_enter_time = datetime.utcnow() - timedelta(days=60)
    db.commit()
    refresh_stage_metrics(db)
    db.commit()

    response = as_sales.get("/api/projects", params={"stagnant_only": True})
    assert response.status_code == 200
    assert all(item["stage_stagnant"] for item in response.json()["items"])


# ==========================================================================
# 漏斗与列表
# ==========================================================================
def test_funnel_returns_eight_stages(as_sales):
    response = as_sales.get("/api/projects/funnel")
    assert response.status_code == 200
    stages = response.json()["stages"]
    assert len(stages) == 8
    assert [s["stage"] for s in stages] == [s.value for s in ProjectStage]


def test_funnel_weighted_revenue(as_sales, customer_id):
    """加权营收 = 各阶段金额 × 该阶段赢率。"""
    create_project(as_sales, customer_id)

    projects = as_sales.get("/api/projects", params={"ongoing_only": True}).json()["items"]
    opportunity_projects = [p for p in projects if p["stage"] == "opportunity"]
    expected_total = sum(p["est_total_revenue"] or 0 for p in opportunity_projects)

    stages = as_sales.get("/api/projects/funnel").json()["stages"]
    opportunity = next(s for s in stages if s["stage"] == "opportunity")

    assert opportunity["count"] == len(opportunity_projects)
    assert opportunity["weighted_revenue"] == pytest.approx(
        expected_total * 0.10, rel=0.01
    )


def test_project_list_filters_by_stage(as_sales, customer_id):
    project = create_project(as_sales, customer_id).json()
    fill_gate(as_sales, project["id"], ProjectStage.OPPORTUNITY)
    as_sales.post(f"/api/projects/{project['id']}/advance", json={})

    response = as_sales.get("/api/projects", params={"stage": "tech_eval"})
    assert response.status_code == 200
    assert all(item["stage"] == "tech_eval" for item in response.json()["items"])


def test_project_list_keyword(as_sales, customer_id):
    create_project(as_sales, customer_id, project_name="华智穿戴海外版")
    response = as_sales.get("/api/projects", params={"keyword": "穿戴"})
    assert response.json()["total"] >= 1


def test_project_visits_timeline(as_sales, customer_id):
    """需求文档 5.2：项目详情页展示关联拜访。"""
    project = create_project(as_sales, customer_id).json()
    as_sales.post(
        "/api/visits/checkin",
        json={
            "customer_id": customer_id,
            "longitude": 113.9445,
            "latitude": 22.5252,
            "address": "深圳南山",
            "project_id": project["id"],
        },
    )

    response = as_sales.get(f"/api/projects/{project['id']}/visits")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
