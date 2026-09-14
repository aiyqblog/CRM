"""表单字段名与路由参数名的一致性检查。

背景（真实故障）：
    模板写成 ``<input name="close_reason">``，而路由处理函数的参数命名是
    ``reason``。FastAPI 对缺失的 ``Form`` 字段**只取默认值、不报任何错**，
    于是「关闭原因」永远是空串，接口抛出 R-23「关闭原因必填」——
    错误信息把人引向「用户没填」，而真正的问题是后端根本没收到这个字段。

    这类 bug 用普通接口测试抓不到（测试直接构造请求体，用的是后端认可的名字），
    只有真的从渲染结果出发才看得见。所以这里做两件事：
      1. 通用交叉校验：解析渲染出的表单字段，与路由签名逐一比对
      2. 功能验证：按模板声明的字段名走一遍完整流程，断言成功
"""

from __future__ import annotations

import inspect
import re

import pytest

from tests.conftest import PASSWORD

pytestmark = pytest.mark.api

#: 路由签名里由框架注入、不出现在表单中的参数
INJECTED_PARAMS = {"self", "request", "db"}

CONTROL_PATTERN = re.compile(r"<(?:input|select|textarea)[^>]*\bname=\"([^\"]+)\"")
FORM_PATTERN = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.S)


def _collect_forms(html: str) -> dict[str, set[str]]:
    """从渲染结果里抽出 {POST 表单 action: {字段名}}。

    只收 ``method="post"`` 的表单：页面上还有 GET 搜索表单，
    它们的 action 与 POST 路由同名，混进来会误报。
    """
    forms: dict[str, set[str]] = {}
    for attrs, body in FORM_PATTERN.findall(html):
        method_match = re.search(r'\bmethod="([^"]*)"', attrs, re.I)
        method = (method_match.group(1) if method_match else "get").lower()
        if method != "post":
            continue

        action_match = re.search(r'\baction="([^"]+)"', attrs)
        if not action_match:
            continue

        names = set(CONTROL_PATTERN.findall(body))
        forms.setdefault(action_match.group(1), set()).update(names)
    return forms


def _iter_api_routes(routes):
    """递归展开所有 APIRoute。

    FastAPI 0.141 / Starlette 1.6 起，``include_router`` 不再把子路由展平进
    ``app.routes``，而是包成 ``_IncludedRouter`` 节点。因此不能直接遍历
    ``app.routes`` 找路径 —— 必须往下钻一层 ``original_router``。
    """
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _iter_api_routes(original.routes)
            continue
        if getattr(route, "path", None) and hasattr(route, "endpoint"):
            yield route


def _handler_params(action_path: str) -> set[str] | None:
    """按实际 URL 反查处理函数签名中的参数名。"""
    from app.main import app

    for route in _iter_api_routes(app.routes):
        methods = getattr(route, "methods", None) or set()
        if "POST" not in methods:
            continue
        pattern = re.sub(r"\{[^}]+\}", "[^/]+", route.path)
        if re.fullmatch(pattern, action_path):
            signature = inspect.signature(route.endpoint)
            return {name for name in signature.parameters if name not in INJECTED_PARAMS}
    return None


def _login(client):
    client.post("/login", data={"username": "sales_a", "password": PASSWORD})


# ==========================================================================
# 通用交叉校验
# ==========================================================================
def test_customers_page_form_fields_match_handler(client, customer_id):
    _login(client)
    forms = _collect_forms(client.get("/customers").text)
    _assert_fields_match(forms, "/customers")


def test_projects_page_form_fields_match_handler(client, customer_id):
    _login(client)
    forms = _collect_forms(client.get("/projects").text)
    _assert_fields_match(forms, "/projects")


def test_checkin_page_form_fields_match_handler(client, customer_id):
    _login(client)
    forms = _collect_forms(client.get("/visits/new").text)
    _assert_fields_match(forms, "/visits/checkin")


def test_project_detail_forms_match_handlers(client, customer_id):
    """项目详情页有 4 个表单：产出物、推进、回退、关闭。"""
    project = _create_project(client, customer_id)
    forms = _collect_forms(client.get(f"/projects/{project}").text)

    assert forms, "项目详情页没有解析到任何表单"
    for action in forms:
        _assert_fields_match(forms, action)

    actions = set(forms)
    assert any(a.endswith("/advance") for a in actions)
    assert any(a.endswith("/close") for a in actions)
    assert any("/gate/" in a for a in actions)


def test_visit_detail_checkout_form_matches_handler(client, customer_id):
    _login(client)
    response = client.post(
        "/visits/checkin",
        data={"customer_id": str(customer_id), "visit_type": "2", "is_mocked": "0",
              "address": "", "longitude": "", "latitude": "", "plan_id": ""},
    )
    record_id = response.headers["location"].split("/")[2].split("?")[0]

    forms = _collect_forms(client.get(f"/visits/{record_id}").text)
    assert any(a.endswith("/checkout") for a in forms)
    for action in forms:
        _assert_fields_match(forms, action)


def _assert_fields_match(forms: dict[str, set[str]], action: str) -> None:
    fields = forms.get(action)
    assert fields is not None, f"未在渲染结果里找到 action={action} 的表单"

    params = _handler_params(action)
    assert params is not None, f"找不到能处理 {action} 的 POST 路由"

    unknown = fields - params
    assert not unknown, (
        f"表单 {action} 提交了后端不认识的字段：{sorted(unknown)}。\n"
        f"后端参数：{sorted(params)}\n"
        "FastAPI 对多余的表单字段会静默丢弃，对缺失的字段只取默认值 —— "
        "两边名字不一致时不会报错，而是变成空串触发误导性的业务错误。"
    )


# ==========================================================================
# 功能验证：按模板声明的字段名走完整流程
# ==========================================================================
def _create_project(client, customer_id) -> int:
    client.post("/login", data={"username": "sales_a", "password": PASSWORD})
    response = client.post(
        "/projects",
        data={
            "project_name": "契约测试项目",
            "customer_id": str(customer_id),
            "project_type": "1",
            "channel_type": "1",
        },
    )
    assert response.status_code == 303
    return int(response.headers["location"].split("/")[2].split("?")[0])


def test_close_form_actually_receives_reason(client, customer_id):
    """回归用例：关闭表单的字段名必须真的被后端收到。"""
    project_id = _create_project(client, customer_id)

    response = client.post(
        f"/projects/{project_id}/close",
        data={
            "close_type": "1",
            "close_reason": "客户已完成签约并下达首笔量产订单",
            "lost_to_competitor": "",
        },
    )
    assert response.status_code == 303
    location = response.headers["location"]

    assert "R-23" not in location, "关闭原因没有被后端收到（字段名不一致）"
    assert client.get(f"/api/projects/{project_id}").json()["status"] == 2


def test_close_form_reason_is_persisted(client, customer_id):
    project_id = _create_project(client, customer_id)
    reason = "客户已完成签约并下达首笔量产订单"

    client.post(
        f"/projects/{project_id}/close",
        data={"close_type": "1", "close_reason": reason, "lost_to_competitor": ""},
    )

    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["close_reason"] == reason
    assert detail["close_type"] == 1


def test_rollback_form_actually_receives_reason(client, customer_id):
    """回归用例：回退表单的字段名必须真的被后端收到。"""
    project_id = _create_project(client, customer_id)
    client.post(
        f"/projects/{project_id}/gate/G1-01", data={"content": "客户需求说明内容"}
    )
    client.post(f"/projects/{project_id}/advance", data={"remark": "材料齐备"})

    reason = "客户重新评估技术方案，需要退回上一阶段补充选型对比材料"
    response = client.post(
        f"/projects/{project_id}/rollback", data={"reason": reason}
    )
    assert response.status_code == 303
    assert "R-22" not in response.headers["location"]

    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["stage"] == "opportunity"


def test_advance_form_remark_is_persisted(client, customer_id):
    project_id = _create_project(client, customer_id)
    client.post(f"/projects/{project_id}/gate/G1-01", data={"content": "需求说明"})

    remark = "契约测试推进备注"
    client.post(f"/projects/{project_id}/advance", data={"remark": remark})

    detail = client.get(f"/api/projects/{project_id}").json()
    latest = detail["stage_history"][0]
    assert latest["remark"] == remark


def test_gate_form_content_is_persisted(client, customer_id):
    project_id = _create_project(client, customer_id)
    content = "客户确认需要 AI 模组，年用量 20 万片"

    client.post(f"/projects/{project_id}/gate/G1-01", data={"content": content})

    detail = client.get(f"/api/projects/{project_id}").json()
    item = next(i for i in detail["gate_items"] if i["item_code"] == "G1-01")
    assert item["content"] == content
    assert item["status"] == 1


def test_checkout_form_fields_are_persisted(client, customer_id):
    _login(client)
    checkin = client.post(
        "/visits/checkin",
        data={"customer_id": str(customer_id), "visit_type": "2", "is_mocked": "0",
              "address": "", "longitude": "", "latitude": "", "plan_id": ""},
    )
    record_id = checkin.headers["location"].split("/")[2].split("?")[0]

    content = "本次拜访与客户确认了模组选型方向，客户同意先做小批量验证。"
    feedback = "客户关注功耗表现"
    action = "下周提供样品"

    response = client.post(
        f"/visits/{record_id}/checkout",
        data={"content": content, "customer_feedback": feedback, "next_action": action},
    )
    assert response.status_code == 303
    assert "R-11" not in response.headers["location"]

    detail = client.get(f"/api/visits/{record_id}").json()
    assert detail["content"] == content
    assert detail["customer_feedback"] == feedback
    assert detail["next_action"] == action


def test_checkin_form_fields_are_persisted(client, customer_id):
    """Issue #9：按模板 name 提交三字段，读 /api/visits/{id} 断言值真的入库。

    比「通用交叉校验」更强一层：交叉校验只证明名字没写错，
    这里证明值真的走到了库里 —— 字段名写错时 FastAPI 只取默认值、
    不报错，字段会静静变成空（B1 红线）。
    """
    _login(client)
    receptionist = "现场接待人张经理"
    content = "签到阶段记录的沟通事项：客户介绍了当前产线情况。"
    next_action = "签到阶段登记的后续推进计划"

    response = client.post(
        "/visits/checkin",
        data={
            "customer_id": str(customer_id),
            "visit_type": "2",
            "is_mocked": "0",
            "address": "",
            "longitude": "",
            "latitude": "",
            "plan_id": "",
            "receptionist": receptionist,
            "content": content,
            "next_action": next_action,
        },
    )
    assert response.status_code == 303
    record_id = response.headers["location"].split("/")[2].split("?")[0]

    detail = client.get(f"/api/visits/{record_id}").json()
    assert detail["receptionist"] == receptionist
    assert detail["content"] == content
    assert detail["next_action"] == next_action


def test_customer_form_fields_are_persisted(client, customer_id):
    _login(client)
    response = client.post(
        "/customers",
        data={
            "name": "契约测试客户",
            "industry": "工业网关",
            "level": "B",
            "address": "苏州市工业园区",
            "contact_name": "黄工",
            "contact_phone": "13700000000",
        },
    )
    assert response.status_code == 303

    customer_id_new = int(response.headers["location"].split("/")[2].split("?")[0])
    detail = client.get(f"/api/customers/{customer_id_new}").json()
    assert detail["name"] == "契约测试客户"
    assert detail["industry"] == "工业网关"
    assert detail["contact_name"] == "黄工"


def test_project_form_amounts_are_persisted(client, customer_id):
    _login(client)
    response = client.post(
        "/projects",
        data={
            "project_name": "契约金额项目",
            "customer_id": str(customer_id),
            "project_type": "2",
            "channel_type": "3",
            "unit_price": "12.5",
            "est_annual_qty": "200000",
            "lifecycle_years": "3",
            "applied_industry": "智能穿戴",
            "competitor": "竞品 Z",
        },
    )
    assert response.status_code == 303
    project_id = int(response.headers["location"].split("/")[2].split("?")[0])

    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["unit_price"] == 12.5
    assert detail["est_annual_qty"] == 200000
    assert detail["lifecycle_years"] == 3
    assert detail["est_total_revenue"] == 7_500_000.00
    assert detail["project_type"] == 2
    assert detail["channel_type"] == 3
    assert detail["competitor"] == "竞品 Z"


def test_project_new_fields_are_persisted(client, customer_id):
    """Issue #5 新增栏位的字段名必须真的被后端收到。

    比「通用交叉校验」更强一层：交叉校验只能证明名字没写错，
    这里证明的是「值真的走到了库里」。名字写错时 FastAPI 只取默认值、
    不报错，字段会静静变成空 —— 只有把值读回来才看得见。
    """
    _login(client)
    response = client.post(
        "/projects",
        data={
            "project_name": "契约新字段项目",
            "customer_id": str(customer_id),
            "project_type": "1",
            "channel_type": "1",
            "project_category": "2",
            "product_series": "GNSS定位天线",
            "product_model": "YEGB000Q1A",
            "expected_dwin_date": "2026-11-20",
        },
    )
    assert response.status_code == 303
    project_id = int(response.headers["location"].split("/")[2].split("?")[0])

    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["project_category"] == 2
    assert detail["project_category_label"] == "小型项目"
    assert detail["product_series"] == "GNSS定位天线"
    assert detail["product_model"] == "YEGB000Q1A"
    assert detail["expected_dwin_date"] == "2026-11-20"


def test_project_new_fields_are_optional_from_form(client, customer_id):
    """四个新栏位都允许留空 —— 表单不填也必须能建项目。"""
    _login(client)
    response = client.post(
        "/projects",
        data={
            "project_name": "契约空新字段项目",
            "customer_id": str(customer_id),
            "project_type": "1",
            "channel_type": "1",
            "project_category": "",
            "product_series": "",
            "product_model": "",
            "expected_dwin_date": "",
        },
    )
    assert response.status_code == 303
    project_id = int(response.headers["location"].split("/")[2].split("?")[0])

    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["project_category"] is None
    assert detail["product_series"] is None
    assert detail["product_model"] is None
    assert detail["expected_dwin_date"] is None


def test_project_form_rejects_mismatched_model(client, customer_id):
    """表单层同样要挡跨系列型号 —— 前端联动被绕过时的兜底。"""
    _login(client)
    response = client.post(
        "/projects",
        data={
            "project_name": "契约错配项目",
            "customer_id": str(customer_id),
            "project_type": "1",
            "channel_type": "1",
            "product_series": "卫星通信天线",
            "product_model": "YECT005W1A",
        },
    )

    assert response.status_code == 303
    assert "R-31" in response.headers["location"]


def test_project_form_rejects_bad_date(client, customer_id):
    """日期格式非法时给出 R-32，而不是静默丢字段。"""
    _login(client)
    response = client.post(
        "/projects",
        data={
            "project_name": "契约坏日期项目",
            "customer_id": str(customer_id),
            "project_type": "1",
            "channel_type": "1",
            "expected_dwin_date": "2026/11/20",
        },
    )

    assert response.status_code == 303
    assert "R-32" in response.headers["location"]
