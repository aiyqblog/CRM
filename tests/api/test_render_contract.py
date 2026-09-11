"""下拉框与枚举渲染的回归防护。

背景（真实故障）：
    ``<option value="{{ key }}">`` 里的 ``key`` 是 Python 枚举成员。
    对于 ``class VisitType(int, Enum)`` 这类**混入枚举**，``str()`` 返回的是
    ``"VisitType.ROUTINE"`` 而不是 ``2`` —— 只有 ``IntEnum`` 才返回数值。

    后果：浏览器选中「例行拜访」后提交，服务端收到字符串，直接 422。
    而接口测试与页面测试**都发现不了**，因为它们在构造请求时硬编码了整数 2，
    从未真正读取页面渲染出来的值。这个 bug 只在真人用浏览器操作时暴露。

本文件的用例专门补上这个缺口：直接解析渲染结果，检查表单控件提交出去的值。
成本近乎为零，却能挡住一整类「后端对、前端也『对』、就是跑不通」的问题。
"""

from __future__ import annotations

import re

import pytest

from tests.conftest import PASSWORD

pytestmark = pytest.mark.api

#: 任何形如 "VisitType.ROUTINE" / "ProjectStage.WON" 的值都是坏的
ENUM_REPR_PATTERN = re.compile(
    r"^(VisitType|ProjectType|ProjectCategory|ChannelType|ProjectStage|Role"
    r"|PlanStatus|LocationStatus)\."
)


def _option_values(html: str) -> list[str]:
    return re.findall(r'<option\s+value="([^"]*)"', html)


def _login_form(client):
    client.post("/login", data={"username": "sales_a", "password": PASSWORD})


def _assert_no_enum_repr(values: list[str], page: str) -> None:
    bad = [v for v in values if ENUM_REPR_PATTERN.match(v)]
    assert not bad, (
        f"{page} 的下拉框把枚举成员直接当成了 value：{bad}。"
        "模板里应使用裸值（见 app/web/pages.py 的 _plain_keyed）。"
    )


def test_checkin_form_option_values_are_plain(client, customer_id):
    _login_form(client)
    response = client.get("/visits/new")
    assert response.status_code == 200

    values = _option_values(response.text)
    assert values, "签到表单没有任何 option"

    _assert_no_enum_repr(values, "发起拜访页")

    # 拜访类型必须是 1~5 的整数，且都能被服务端接受
    type_values = re.findall(
        r'<select[^>]*name="visit_type"[^>]*>(.*?)</select>', response.text, re.S
    )
    assert type_values
    option_values = _option_values(type_values[0])
    assert all(v.isdigit() for v in option_values), f"拜访类型不是整数：{option_values}"
    assert set(option_values) == {"1", "2", "3", "4", "5"}


def test_project_form_option_values_are_plain(client, customer_id):
    _login_form(client)
    response = client.get("/projects")

    values = _option_values(response.text)
    assert values
    _assert_no_enum_repr(values, "项目列表页")

    for name in ("project_type", "channel_type"):
        block = re.findall(
            rf'<select[^>]*name="{name}"[^>]*>(.*?)</select>', response.text, re.S
        )
        assert block, f"未找到 {name} 下拉框"
        option_values = _option_values(block[0])
        assert all(v.isdigit() for v in option_values), f"{name} 不是整数：{option_values}"


def test_stage_filter_links_use_plain_values(client, customer_id):
    """阶段筛选链接必须是 ?stage=opportunity 这种裸值。"""
    _login_form(client)
    html = client.get("/projects").text

    hrefs = re.findall(r'href="/projects\?stage=([^"&]*)', html)
    assert hrefs
    _assert_no_enum_repr(hrefs, "阶段筛选")
    assert "opportunity" in hrefs


def test_project_category_options_are_plain(client, customer_id):
    """Issue #5：项目类别下拉渲染的必须是 1/2 这类裸值。"""
    _login_form(client)
    html = client.get("/projects").text

    block = re.findall(
        r'<select[^>]*name="project_category"[^>]*>(.*?)</select>', html, re.S
    )
    assert block, "未找到项目类别下拉框"
    values = _option_values(block[0])
    _assert_no_enum_repr(values, "项目类别")
    assert set(values) == {"1", "2"}
    assert "大型项目" in block[0] and "小型项目" in block[0]


def test_product_series_options_match_server_side_list(client, customer_id):
    """产品系列下拉的选项必须与服务端清单完全一致。

    两者一旦漂移，用户会在页面上选到一个服务端不认的系列，
    提交时收到一个看起来莫名其妙的 R-31。
    """
    from app.constants import PRODUCT_SERIES_MODELS

    _login_form(client)
    html = client.get("/projects").text

    block = re.findall(r'<select[^>]*name="product_series"[^>]*>(.*?)</select>', html, re.S)
    assert block, "未找到产品系列下拉框"

    rendered = [v for v in _option_values(block[0]) if v]
    assert rendered == list(PRODUCT_SERIES_MODELS)


def test_product_model_options_start_empty(client, customer_id):
    """型号下拉由前端联动填充：初始必须是空占位，不能预先塞满某个系列的型号。"""
    _login_form(client)
    html = client.get("/projects").text

    block = re.findall(r'<select[^>]*name="product_model"[^>]*>(.*?)</select>', html, re.S)
    assert block, "未找到产品型号下拉框"

    assert _option_values(block[0]) == [""]
    assert "请先选择产品系列" in block[0]


def test_checkin_form_submits_values_the_api_accepts(client, customer_id):
    """端到端的一致性：把页面渲染出的真实值直接提交回去，服务端必须接受。

    这是比断言「值是整数」更强的一层保障 —— 它验证的是
    页面产物与服务端契约之间的一致性，而不是某个具体写法的正确性。
    """
    _login_form(client)

    html = client.get("/visits/new").text
    type_block = re.findall(
        r'<select[^>]*name="visit_type"[^>]*>(.*?)</select>', html, re.S
    )[0]
    rendered_type = _option_values(type_block)[1]  # 第二个是"例行拜访"

    response = client.post(
        "/visits/checkin",
        data={
            "customer_id": str(customer_id),
            "visit_type": rendered_type,
            "is_mocked": "0",
            "address": "",
            "longitude": "",
            "latitude": "",
            "plan_id": "",
        },
    )
    assert response.status_code == 303, (
        f"页面渲染的 visit_type={rendered_type!r} 被服务端拒绝；"
        f"响应：{response.text[:300]}"
    )


def test_role_label_renders_in_navbar(client):
    """角色徽标不能渲染成 "Role.SALES"。"""
    _login_form(client)
    html = client.get("/").text

    assert 'badge-gray">Role.' not in html
    assert "销售" in html
