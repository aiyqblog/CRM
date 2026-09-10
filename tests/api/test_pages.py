"""页面路由测试（服务端渲染）。

E2E 前的快速防线：浏览器测试失败时，先看这里能否复现 ——
如果这里过了，问题多半出在前端交互而不是后端渲染。
"""

from __future__ import annotations

import pytest

from tests.conftest import PASSWORD

pytestmark = pytest.mark.api


def form_login(client, username="sales_a"):
    response = client.post(
        "/login", data={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 303
    return response


# ==========================================================================
# 登录
# ==========================================================================
def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert 'data-testid="login-form"' in response.text


def test_form_login_redirects_to_dashboard(client):
    response = form_login(client)
    assert response.headers["location"] == "/"


def test_form_login_wrong_password(client):
    response = client.post(
        "/login", data={"username": "sales_a", "password": "wrong"}
    )
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_dashboard_requires_login(client):
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_all_pages_require_login(client):
    for path in ["/", "/customers", "/visits", "/visits/new", "/projects", "/funnel"]:
        response = client.get(path)
        assert response.status_code == 303, f"{path} 未要求登录"
        assert "/login" in response.headers["location"]


def test_logout_redirects(client):
    form_login(client)
    response = client.get("/logout")
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


# ==========================================================================
# 各页面渲染
# ==========================================================================
@pytest.mark.parametrize(
    "path,marker",
    [
        ("/", 'data-testid="nav-dashboard"'),
        ("/customers", 'data-testid="customer-table"'),
        ("/customers", 'data-testid="customer-form"'),
        ("/visits", 'data-testid="new-visit"'),
        ("/visits/new", 'data-testid="checkin-form"'),
        ("/projects", 'data-testid="project-table"'),
        ("/projects", 'data-testid="project-form"'),
        ("/funnel", 'data-testid="funnel-chart"'),
    ],
)
def test_page_renders_expected_markers(client, path, marker):
    form_login(client)
    response = client.get(path)
    assert response.status_code == 200
    assert marker in response.text, f"{path} 缺少 {marker}"


def test_dashboard_shows_metrics(client):
    form_login(client)
    response = client.get("/")
    assert 'data-testid="metric-visits"' in response.text
    assert 'data-testid="metric-abnormal"' in response.text


def test_customer_detail_renders(client, customer_id):
    form_login(client)
    response = client.get(f"/customers/{customer_id}")
    assert response.status_code == 200
    assert 'data-testid="customer-timeline"' in response.text
    assert "深圳华智终端有限公司" in response.text


def test_other_team_customer_page_redirects_with_error(
    client, as_sales_other_team, customer_id
):
    """页面层越权：不返回 403，而是重定向回列表并给出提示。"""
    response = as_sales_other_team.get(f"/customers/{customer_id}")
    assert response.status_code == 303
    assert "/customers" in response.headers["location"]


def test_funnel_shows_all_stages(client):
    form_login(client)
    response = client.get("/funnel")
    for label in ["机会识别", "技术评估", "送样测试", "Design-in", "小批量试产", "商务谈判", "签约量产", "赢单"]:
        assert label in response.text


# ==========================================================================
# 表单流程
# ==========================================================================
def test_create_customer_via_form(client):
    form_login(client)
    response = client.post(
        "/customers",
        data={"name": "表单创建客户", "industry": "智能穿戴", "level": "B"},
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/customers/")

    detail = client.get(location.split("?")[0])
    assert "表单创建客户" in detail.text


def test_checkin_via_form_uses_customer_coords(client, customer_id):
    """不传坐标时回落到客户登记坐标 —— Web 端无 GPS 时的兜底路径。"""
    form_login(client)
    response = client.post(
        "/visits/checkin",
        data={"customer_id": customer_id, "visit_type": "2", "is_mocked": "0", "address": ""},
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/visits/")

    record_id = response.headers["location"].split("/")[2].split("?")[0]
    detail = client.get(f"/visits/{record_id}")
    assert "定位状态" in detail.text


def test_checkin_via_form_mocked_location(client, customer_id):
    form_login(client)
    response = client.post(
        "/visits/checkin",
        data={"customer_id": customer_id, "visit_type": "2", "is_mocked": "1", "address": ""},
    )
    record_id = response.headers["location"].split("/")[2].split("?")[0]

    detail = client.get(f"/visits/{record_id}")
    assert "模拟位置" in detail.text
    assert 'data-testid="visit-abnormal"' in detail.text


def test_checkout_via_form_short_content_shows_error(client, customer_id):
    form_login(client)
    checkin = client.post(
        "/visits/checkin",
        data={"customer_id": customer_id, "visit_type": "2", "is_mocked": "0", "address": ""},
    )
    record_id = checkin.headers["location"].split("/")[2].split("?")[0]

    response = client.post(f"/visits/{record_id}/checkout", data={"content": "太短"})
    assert response.status_code == 303
    assert "R-11" in response.headers["location"]


def test_checkout_via_form_success(client, customer_id):
    form_login(client)
    checkin = client.post(
        "/visits/checkin",
        data={"customer_id": customer_id, "visit_type": "2", "is_mocked": "0", "address": ""},
    )
    record_id = checkin.headers["location"].split("/")[2].split("?")[0]

    response = client.post(
        f"/visits/{record_id}/checkout",
        data={
            "content": "本次拜访与客户确认了模组选型方向，客户同意先做小批量验证。",
            "next_action": "下周提供样品",
        },
    )
    assert response.status_code == 303

    detail = client.get(f"/visits/{record_id}")
    assert "已完成" in detail.text
    assert 'data-testid="visit-content"' in detail.text


def test_create_project_via_form(client, customer_id):
    form_login(client)
    response = client.post(
        "/projects",
        data={
            "project_name": "表单创建项目",
            "customer_id": customer_id,
            "unit_price": "10",
            "est_annual_qty": "50000",
            "lifecycle_years": "2",
            "project_type": "1",
            "channel_type": "1",
        },
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/projects/")

    project_id = response.headers["location"].split("/")[2].split("?")[0]
    detail = client.get(f"/projects/{project_id}")
    assert "表单创建项目" in detail.text
    assert 'data-testid="stage-bar"' in detail.text
    assert 'data-testid="gate-item-G1-01"' in detail.text


def test_advance_via_form_blocked_by_gate(client, customer_id):
    form_login(client)
    created = client.post(
        "/projects",
        data={"project_name": "门禁测试项目", "customer_id": customer_id},
    )
    project_id = created.headers["location"].split("/")[2].split("?")[0]

    response = client.post(f"/projects/{project_id}/advance", data={})
    assert response.status_code == 303
    assert "R-20" in response.headers["location"]


def test_gate_submit_then_advance_via_form(client, customer_id):
    form_login(client)
    created = client.post(
        "/projects",
        data={"project_name": "门禁通过项目", "customer_id": customer_id},
    )
    project_id = created.headers["location"].split("/")[2].split("?")[0]

    submit = client.post(
        f"/projects/{project_id}/gate/G1-01",
        data={"content": "客户确认需要 AI 模组，年用量 20 万片"},
    )
    assert submit.status_code == 303

    advance = client.post(f"/projects/{project_id}/advance", data={"remark": "材料齐备"})
    assert advance.status_code == 303

    detail = client.get(f"/projects/{project_id}")
    assert "技术评估" in detail.text
    assert 'data-testid="stage-history"' in detail.text


def test_marketing_cannot_see_create_forms(client):
    form_login(client, "market")
    response = client.get("/customers")
    assert response.status_code == 200
    assert 'data-testid="customer-form"' not in response.text
