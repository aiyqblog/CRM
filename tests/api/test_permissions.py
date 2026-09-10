"""数据权限与越权防护专项测试。

需求文档 3.2 权限矩阵的「越权测试点」在这里逐条验证。
这类测试的价值高于业务功能测试 —— 权限漏洞不会让用户报错，
只会让数据悄悄泄露出去。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.api


# ==========================================================================
# 客户可见性
# ==========================================================================
def test_sales_cannot_read_peer_customer_within_same_team(
    as_sales, as_sales_same_team, fresh_database
):
    """同组同事之间也互相看不到对方客户 —— 行级权限按 owner 而非 team。"""
    peer_customer = fresh_database["customers"]["CUS-HZ-004"]  # 属于 sales_a2

    forbidden = as_sales.get(f"/api/customers/{peer_customer}")
    assert forbidden.status_code == 403

    allowed = as_sales_same_team.get(f"/api/customers/{peer_customer}")
    assert allowed.status_code == 200


def test_customer_list_excludes_other_users_data(as_sales, fresh_database):
    response = as_sales.get("/api/customers")
    visible_ids = {item["id"] for item in response.json()["items"]}
    other_team_customer = fresh_database["customers"]["CUS-SZ-003"]

    assert other_team_customer not in visible_ids


def test_manager_sees_whole_team(as_manager, fresh_database):
    response = as_manager.get("/api/customers")
    visible_ids = {item["id"] for item in response.json()["items"]}

    assert fresh_database["customers"]["CUS-HZ-001"] in visible_ids  # sales_a
    assert fresh_database["customers"]["CUS-HZ-004"] in visible_ids  # sales_a2
    assert fresh_database["customers"]["CUS-SZ-003"] not in visible_ids  # 另一团队


def test_director_sees_all_teams(as_director, fresh_database):
    response = as_director.get("/api/customers")
    visible_ids = {item["id"] for item in response.json()["items"]}
    assert set(fresh_database["customers"].values()) <= visible_ids


def test_manager_cannot_read_other_team_customer(as_manager, fresh_database):
    other_team_customer = fresh_database["customers"]["CUS-SZ-003"]
    assert as_manager.get(f"/api/customers/{other_team_customer}").status_code == 403


# ==========================================================================
# 拜访可见性
# ==========================================================================
def test_visit_list_is_scoped(as_sales, as_sales_other_team, customer_id):
    as_sales.post(
        "/api/visits/checkin",
        json={
            "customer_id": customer_id,
            "longitude": 113.9445,
            "latitude": 22.5252,
            "address": "深圳南山",
        },
    )

    assert as_sales.get("/api/visits").json()["total"] >= 1
    assert as_sales_other_team.get("/api/visits").json()["total"] == 0


def test_cannot_read_peer_visit_detail(as_sales, as_sales_other_team, customer_id):
    record = as_sales.post(
        "/api/visits/checkin",
        json={
            "customer_id": customer_id,
            "longitude": 113.9445,
            "latitude": 22.5252,
            "address": "深圳南山",
        },
    ).json()

    assert as_sales.get(f"/api/visits/{record['id']}").status_code == 200
    assert (
        as_sales_other_team.get(f"/api/visits/{record['id']}").status_code == 403
    )


def test_cannot_checkout_peer_visit(as_sales, as_sales_other_team, customer_id):
    record = as_sales.post(
        "/api/visits/checkin",
        json={
            "customer_id": customer_id,
            "longitude": 113.9445,
            "latitude": 22.5252,
            "address": "深圳南山",
        },
    ).json()

    response = as_sales_other_team.post(
        f"/api/visits/{record['id']}/checkout",
        json={"content": "冒名提交的拜访纪要内容足够长可以绕过字数校验"},
    )
    assert response.status_code == 403


def test_manager_sees_team_visits(as_sales, as_manager, customer_id):
    as_sales.post(
        "/api/visits/checkin",
        json={
            "customer_id": customer_id,
            "longitude": 113.9445,
            "latitude": 22.5252,
            "address": "深圳南山",
        },
    )
    assert as_manager.get("/api/visits").json()["total"] >= 1


# ==========================================================================
# 项目可见性
# ==========================================================================
def test_cannot_read_peer_project(as_sales, as_sales_other_team, fresh_database):
    other_customer = fresh_database["customers"]["CUS-SZ-003"]
    project = as_sales_other_team.post(
        "/api/projects",
        json={"project_name": "华东项目", "customer_id": other_customer},
    ).json()

    assert (
        as_sales.get(f"/api/projects/{project['id']}").status_code == 403
    )
    assert (
        as_sales.get(f"/api/projects/{project['id']}/gate-check").status_code == 403
    )


def test_cannot_advance_peer_project(as_sales, as_sales_other_team, fresh_database):
    other_customer = fresh_database["customers"]["CUS-SZ-003"]
    project = as_sales_other_team.post(
        "/api/projects",
        json={"project_name": "华东项目", "customer_id": other_customer},
    ).json()

    response = as_sales.post(f"/api/projects/{project['id']}/advance", json={})
    assert response.status_code == 403


def test_cannot_close_peer_project(as_sales, as_sales_other_team, fresh_database):
    other_customer = fresh_database["customers"]["CUS-SZ-003"]
    project = as_sales_other_team.post(
        "/api/projects",
        json={"project_name": "华东项目", "customer_id": other_customer},
    ).json()

    response = as_sales.post(
        f"/api/projects/{project['id']}/close",
        json={"close_type": 3, "reason": "试图关闭别人的项目"},
    )
    assert response.status_code == 403


def test_project_list_scoped(as_sales, as_sales_other_team, fresh_database):
    other_customer = fresh_database["customers"]["CUS-SZ-003"]
    as_sales_other_team.post(
        "/api/projects",
        json={"project_name": "华东项目", "customer_id": other_customer},
    )
    names = [item["project_name"] for item in as_sales.get("/api/projects").json()["items"]]
    assert "华东项目" not in names


def test_manager_can_view_team_project(as_sales, as_manager, customer_id):
    project = as_sales.post(
        "/api/projects",
        json={"project_name": "组内项目", "customer_id": customer_id},
    ).json()
    assert as_manager.get(f"/api/projects/{project['id']}").status_code == 200


# ==========================================================================
# 只读角色
# ==========================================================================
def test_marketing_is_read_only(as_marketing, customer_id):
    """市场角色可看聚合数据，但不能写。"""
    assert as_marketing.get("/api/customers").status_code == 200
    assert as_marketing.get("/api/projects/funnel").status_code == 200

    assert (
        as_marketing.post("/api/customers", json={"name": "只读角色写入"}).status_code
        == 400
    )
    assert (
        as_marketing.post(
            "/api/visits/checkin",
            json={
                "customer_id": customer_id,
                "longitude": 113.9445,
                "latitude": 22.5252,
                "address": "深圳南山",
            },
        ).status_code
        == 400
    )


def test_delete_customer_not_supported(as_admin, customer_id):
    """当前版本不提供客户删除接口，避免误删主数据。"""
    response = as_admin.delete(f"/api/customers/{customer_id}")
    assert response.status_code in (404, 405)


# ==========================================================================
# 会话
# ==========================================================================
def test_anonymous_cannot_reach_any_api(client, customer_id):
    endpoints = [
        "/api/customers",
        f"/api/customers/{customer_id}",
        "/api/visits",
        "/api/projects",
        "/api/projects/funnel",
        "/api/visit-plans",
    ]
    for endpoint in endpoints:
        response = client.get(endpoint)
        assert response.status_code == 403, f"{endpoint} 未拦截匿名访问"
        assert response.json()["code"] == "AUTH-401"


def test_anonymous_cannot_mutate(client, customer_id):
    assert client.post("/api/customers", json={"name": "x"}).status_code == 403
    assert (
        client.post(
            "/api/visits/checkin",
            json={
                "customer_id": customer_id,
                "longitude": 1.0,
                "latitude": 1.0,
                "address": "x",
            },
        ).status_code
        == 403
    )
    assert (
        client.post("/api/projects", json={"project_name": "x", "customer_id": customer_id}).status_code
        == 403
    )
