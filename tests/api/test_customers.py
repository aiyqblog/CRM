"""客户接口与越权防护测试。"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.api


def test_list_customers(as_sales):
    response = as_sales.get("/api/customers")
    assert response.status_code == 200
    assert response.json()["total"] >= 1


def test_create_customer(as_sales):
    response = as_sales.post(
        "/api/customers",
        json={
            "name": "新建测试客户",
            "industry": "智能家居",
            "level": "A",
            "address": "深圳市宝安区某路 1 号",
            "longitude": 113.88,
            "latitude": 22.55,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "新建测试客户"
    assert body["owner_id"] is not None


def test_create_customer_requires_login(client):
    response = client.post("/api/customers", json={"name": "X"})
    assert response.status_code == 403


def test_create_customer_validation_error(as_sales):
    response = as_sales.post("/api/customers", json={"name": ""})
    assert response.status_code == 422
    assert response.json()["code"] == "REQ-422"


def test_get_customer_detail(as_sales, customer_id):
    response = as_sales.get(f"/api/customers/{customer_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "深圳华智终端有限公司"


def test_get_nonexistent_customer(as_sales):
    response = as_sales.get("/api/customers/999999")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT-FOUND"


def test_cannot_read_other_team_customer(as_sales_other_team, customer_id):
    """越权测试点：销售 B 不可查看归属于销售 A 的客户。"""
    response = as_sales_other_team.get(f"/api/customers/{customer_id}")
    assert response.status_code == 403
    assert response.json()["code"] == "PERM-403"


def test_cannot_read_other_team_customer_timeline(
    as_sales_other_team, customer_id
):
    response = as_sales_other_team.get(f"/api/customers/{customer_id}/timeline")
    assert response.status_code == 403


def test_same_team_member_visible_to_manager(as_manager, customer_id):
    response = as_manager.get(f"/api/customers/{customer_id}")
    assert response.status_code == 200


def test_director_sees_all_customers(as_director, fresh_database):
    response = as_director.get("/api/customers")
    assert response.status_code == 200
    # 种子共 4 个客户，总监应全部可见
    assert response.json()["total"] == len(fresh_database["customers"])


def test_sales_sees_only_own_customers(as_sales, as_sales_other_team):
    own = as_sales.get("/api/customers").json()["total"]
    other = as_sales_other_team.get("/api/customers").json()["total"]
    assert own >= 1
    assert other >= 1
    assert own != other or own == 1


def test_marketing_cannot_create_customer(as_marketing):
    response = as_marketing.post("/api/customers", json={"name": "只读角色尝试创建"})
    assert response.status_code == 400
    assert response.json()["code"] == "PERM-403"


def test_customer_keyword_filter(as_sales):
    response = as_sales.get("/api/customers", params={"keyword": "华智"})
    assert response.status_code == 200
    items = response.json()["items"]
    assert items
    assert all("华智" in item["name"] for item in items)


def test_timeline_aggregates_visits_and_projects(as_sales, customer_id):
    """客户 360° 视图应聚合拜访与项目（需求文档 5.2）。"""
    as_sales.post(
        "/api/visits/checkin",
        json={
            "customer_id": customer_id,
            "longitude": 113.9445,
            "latitude": 22.5252,
            "address": "深圳南山",
        },
    )
    response = as_sales.get(f"/api/customers/{customer_id}/timeline")
    assert response.status_code == 200
    body = response.json()
    assert len(body["visits"]) >= 1
    assert body["projects"]
    assert body["customer"]["id"] == customer_id
