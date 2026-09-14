"""端到端测试 —— 真实服务 + 真实浏览器。

覆盖需求文档第 7 章的 UAT 验收场景。这些用例回答的是
「一个真人打开浏览器，能不能把这件事做完」，是接口测试回答不了的。

注意：E2E 跑在一个会话级共享的服务进程中，数据会累积。
因此所有造数都用唯一名称，且不依赖「当前有几条数据」。

所有表单提交统一走 ``crm.submit(...)`` —— 它内部等待重定向收敛，
不要自己写 ``click() + wait_for_load_state("networkidle")``，
那样会因点击前页面已处于 networkidle 而立即返回，断言落在旧页面上。
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e

NANSHAN_CUSTOMER = "深圳华智终端有限公司"


def uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ==========================================================================
# 认证
# ==========================================================================
def test_login_redirects_to_dashboard(crm):
    crm.login("sales_a")

    assert crm.current_user == "王销售"
    assert crm.path == "/"
    assert crm.page.get_by_test_id("nav-dashboard").count() == 1


def test_login_failure_shows_error(crm):
    crm.login("sales_a", password="wrong-password")

    assert crm.path == "/login"
    assert crm.page.get_by_test_id("login-error").count() == 1


def test_anonymous_is_redirected_to_login(crm):
    crm.goto("/customers")

    assert crm.path == "/login"


def test_logout_clears_session(crm):
    crm.login("sales_a")
    crm.logout()

    crm.goto("/customers")
    assert crm.path == "/login"


# ==========================================================================
# 导航
# ==========================================================================
def test_navigation_between_pages(crm):
    crm.login("sales_a")

    crm.page.get_by_test_id("nav-customers").click()
    crm.page.wait_for_url("**/customers")
    assert crm.path == "/customers"

    crm.page.get_by_test_id("nav-visits").click()
    crm.page.wait_for_url("**/visits")
    assert crm.path == "/visits"

    crm.page.get_by_test_id("nav-projects").click()
    crm.page.wait_for_url("**/projects")
    assert crm.path == "/projects"

    crm.page.get_by_test_id("nav-funnel").click()
    crm.page.wait_for_url("**/funnel")
    assert crm.contains("销售漏斗")


def test_funnel_page_lists_all_stages(crm):
    crm.login("sales_a")
    crm.goto("/funnel")

    for label in [
        "机会识别", "技术评估", "送样测试", "Design-in",
        "小批量试产", "商务谈判", "签约量产", "赢单",
    ]:
        assert crm.contains(label), f"漏斗缺少阶段 {label}"


# ==========================================================================
# 客户
# ==========================================================================
def test_create_customer_through_ui(crm):
    crm.login("sales_a")
    crm.goto("/customers")

    name = uniq("E2E客户")
    crm.page.get_by_test_id("customer-name").fill(name)
    crm.page.locator("#industry").fill("智能穿戴")
    crm.page.locator("#address").fill("深圳市南山区科技园")
    crm.submit("customer-submit")

    assert crm.path.startswith("/customers/")
    assert crm.contains(name)
    assert "创建成功" in crm.flash


def test_customer_detail_shows_timeline(crm):
    crm.login("sales_a")
    crm.goto("/customers")

    crm.page.get_by_test_id("customer-link").filter(has_text=NANSHAN_CUSTOMER).first.click()
    crm.page.wait_for_url("**/customers/*")

    assert crm.page.get_by_test_id("customer-timeline").count() == 1
    assert crm.page.get_by_test_id("customer-checkin").count() == 1


def test_customer_search(as_sales_crm):
    crm = as_sales_crm
    crm.goto("/customers?keyword=华智")

    rows = crm.page.get_by_test_id("customer-row")
    assert rows.count() >= 1
    assert "华智" in rows.first.inner_text()


# ==========================================================================
# 拜访全流程
# ==========================================================================
def _checkin(crm, *, visit_type: str = "例行拜访", mocked: str = "0"):
    crm.goto("/visits/new")
    crm.page.get_by_test_id("checkin-customer").select_option(label=NANSHAN_CUSTOMER)
    crm.page.get_by_test_id("checkin-type").select_option(label=visit_type)
    crm.page.get_by_test_id("checkin-mocked").select_option(mocked)
    crm.page.get_by_test_id("checkin-receptionist").fill("现场接待人张经理")
    crm.page.get_by_test_id("checkin-content").fill(
        "签到阶段记录的沟通事项：客户介绍了当前产线情况。"
    )
    crm.page.get_by_test_id("checkin-next").fill("签到阶段登记的后续推进计划")
    crm.submit("checkin-submit")
    return crm


def test_full_visit_flow(as_sales_crm):
    """签到 → 签退 → 记录完成。这是外勤场景的主路径。"""
    crm = _checkin(as_sales_crm, visit_type="技术支持")

    assert crm.path.startswith("/visits/")
    assert "签到成功" in crm.flash

    # 纪要过短应被 R-11 拦下
    crm.page.get_by_test_id("checkout-content").fill("太短")
    crm.submit("checkout-submit")
    assert "R-11" in crm.flash

    # 补足内容后提交
    crm.page.get_by_test_id("checkout-content").fill(
        "本次拜访与客户技术负责人确认了 AI 模组选型方向，客户同意先做小批量验证。"
    )
    crm.page.get_by_test_id("checkout-next").fill("下周提供样品与测试报告")
    crm.submit("checkout-submit")

    assert "拜访已提交" in crm.flash
    assert crm.page.get_by_test_id("visit-content").count() == 1
    # Issue #9：客户接待人应在详情页回显
    assert crm.page.get_by_test_id("visit-receptionist").count() == 1
    assert "现场接待人张经理" in crm.page.get_by_test_id("visit-receptionist").inner_text()
    assert "已完成" in crm.page.content()


def test_mocked_location_marks_abnormal(as_sales_crm):
    """选择模拟定位后，记录应被标记异常并进入复核队列。"""
    crm = _checkin(as_sales_crm, mocked="1")

    assert "模拟位置" in crm.page.content()
    assert crm.page.get_by_test_id("visit-abnormal").count() == 1


def test_abnormal_queue_filter(as_sales_crm):
    crm = as_sales_crm
    crm.goto("/visits")

    crm.page.get_by_test_id("filter-abnormal").click()
    crm.page.wait_for_url("**/visits?abnormal_only=1")

    assert "abnormal_only=1" in crm.page.url


def test_customer_timeline_aggregates_after_visit(as_sales_crm):
    """客户 360° 视图应能看到刚提交的拜访（需求文档 5.2）。"""
    crm = _checkin(as_sales_crm)
    marker = uniq("时间轴")

    crm.page.get_by_test_id("checkout-content").fill(
        f"本次沟通重点是 {marker}，客户已确认后续推进节奏。"
    )
    crm.submit("checkout-submit")

    crm.goto("/customers")
    crm.page.get_by_test_id("customer-link").filter(has_text=NANSHAN_CUSTOMER).first.click()
    crm.page.wait_for_url("**/customers/*")

    assert marker in crm.page.get_by_test_id("customer-timeline").inner_text()


# ==========================================================================
# 销售项目：门禁是核心
# ==========================================================================
def _open_project_form(crm):
    crm.goto("/projects")
    crm.page.get_by_test_id("project-name").fill(uniq("E2E项目"))
    crm.page.get_by_test_id("project-customer").select_option(label=NANSHAN_CUSTOMER)
    crm.page.get_by_test_id("project-price").fill("12.5")
    crm.page.get_by_test_id("project-qty").fill("200000")
    crm.page.locator("#lifecycle_years").fill("3")
    crm.submit("project-submit")
    return crm


def test_project_creation_shows_stage_bar_and_gate(as_sales_crm):
    crm = _open_project_form(as_sales_crm)

    assert crm.path.startswith("/projects/")
    assert crm.page.get_by_test_id("stage-bar").count() == 1
    assert crm.page.get_by_test_id("gate-item-G1-01").count() == 1
    assert "机会识别" in crm.page.get_by_test_id("stage-opportunity").inner_text()


def test_project_funnel_updates_after_creation(as_sales_crm):
    crm = _open_project_form(as_sales_crm)
    crm.goto("/funnel")

    assert "机会识别" in crm.page.get_by_test_id("funnel-chart").inner_text()


# ==========================================================================
# Issue #5：创建页新增栏位与产品系列/型号联动
# ==========================================================================
def test_project_form_model_options_follow_series(as_sales_crm):
    """选完产品系列，型号下拉必须变成该系列独有的型号清单。

    这条只能在真浏览器里测：联动是页面上的 JS 行为，
    接口测试与模板静态断言都看不见「切换系列后 DOM 变了没有」。
    """
    crm = as_sales_crm.goto("/projects")
    series = crm.page.get_by_test_id("project-series")
    model = crm.page.get_by_test_id("project-model")

    # 初始状态：还没选系列，型号下拉只有占位项
    assert model.locator("option").count() == 1
    assert model.locator("option").first.inner_text() == "请先选择产品系列"

    series.select_option(label="卫星通信天线")
    assert model.locator("option").all_inner_texts() == [
        "请选择产品型号",
        "YFTA009E3AM",
        "YEGM023AA",
    ]

    # 换一个系列，清单必须整体换掉 —— 不能残留上一个系列的型号
    series.select_option(label="GNSS定位天线")
    options = model.locator("option").all_inner_texts()
    assert "YFTA009E3AM" not in options
    assert {"YFGC007E3A", "YEGT000W8A"} <= set(options)


def test_project_with_new_fields_is_created(as_sales_crm):
    """带新栏位走完整创建流程，并确认值真的落了库（读接口回验）。"""
    crm = as_sales_crm.goto("/projects")
    name = uniq("E2E新栏位")
    crm.page.get_by_test_id("project-name").fill(name)
    crm.page.get_by_test_id("project-customer").select_option(label=NANSHAN_CUSTOMER)
    crm.page.get_by_test_id("project-category").select_option(label="大型项目")
    crm.page.get_by_test_id("project-dwin").fill("2026-12-01")
    crm.page.get_by_test_id("project-series").select_option(label="5G/4G蜂窝天线")
    crm.page.get_by_test_id("project-model").select_option("YECT028W1A")

    crm.submit("project-submit")

    assert crm.path.startswith("/projects/")
    project_id = crm.path.rsplit("/", 1)[-1]

    detail = crm.page.evaluate(
        f"() => fetch('/api/projects/{project_id}').then(r => r.json())"
    )
    assert detail["project_category"] == 1
    assert detail["project_category_label"] == "大型项目"
    assert detail["product_series"] == "5G/4G蜂窝天线"
    assert detail["product_model"] == "YECT028W1A"
    assert detail["expected_dwin_date"] == "2026-12-01"


def test_advance_blocked_by_gate_then_succeeds(as_sales_crm):
    """门禁机制主路径：缺产出物被拦 → 补齐 → 推进成功。"""
    crm = _open_project_form(as_sales_crm)

    crm.submit("advance-submit")
    assert "R-20" in crm.flash
    crm.page.get_by_test_id("gate-form-G1-01").locator("input").fill(
        "客户确认需要 AI 模组，年用量 20 万片"
    )
    crm.submit("gate-submit-G1-01")
    assert "已提交" in crm.flash

    crm.submit("advance-submit")
    assert "技术评估" in crm.flash
    assert "技术评估" in crm.page.get_by_test_id("stage-tech_eval").inner_text()
    assert crm.page.get_by_test_id("stage-history").count() == 1


def test_rollback_requires_reason(as_sales_crm):
    crm = _open_project_form(as_sales_crm)

    crm.page.get_by_test_id("gate-form-G1-01").locator("input").fill("需求说明内容")
    crm.submit("gate-submit-G1-01")
    crm.submit("advance-submit")

    crm.page.get_by_test_id("rollback-reason").fill("太短")
    crm.submit("rollback-submit")
    assert "R-22" in crm.flash

    crm.page.get_by_test_id("rollback-reason").fill(
        "客户重新评估技术方案，需要退回上一阶段补充选型对比材料"
    )
    crm.submit("rollback-submit")
    assert "回退至" in crm.flash
    assert "机会识别" in crm.page.get_by_test_id("stage-opportunity").inner_text()


def test_close_project_lost_requires_competitor(as_sales_crm):
    crm = _open_project_form(as_sales_crm)
    reason = "客户选择了其他供应商"

    crm.page.get_by_test_id("close-type").select_option("2")
    crm.page.get_by_test_id("close-reason").fill(reason)
    crm.submit("close-submit")
    assert "R-24" in crm.flash

    # 提交被拒后页面整页重载，表单字段已被清空 —— 必须连原因一起重填，
    # 否则拿到的是 R-23「关闭原因必填」，看起来像产品出问题，其实是自己漏填。
    crm.page.get_by_test_id("close-type").select_option("2")
    crm.page.get_by_test_id("close-reason").fill(reason)
    crm.page.get_by_test_id("close-competitor").fill("竞品 X")
    crm.submit("close-submit")
    assert "项目已关闭" in crm.flash


def test_manager_can_override_gate(as_manager_crm):
    """主管用覆盖理由绕过门禁，历史里必须留下覆盖痕迹。"""
    crm = as_manager_crm
    crm.goto("/projects")
    crm.page.get_by_test_id("project-name").fill(uniq("覆盖E2E"))
    crm.page.get_by_test_id("project-customer").select_option(label=NANSHAN_CUSTOMER)
    crm.submit("project-submit")

    assert crm.page.get_by_test_id("advance-override").count() == 1

    crm.page.get_by_test_id("advance-override").fill(
        "客户已口头确认需求但内部立项流程尚未走完，为配合客户排期先行推进，材料本周内补齐归档。"
    )
    crm.submit("advance-submit")

    assert "技术评估" in crm.flash
    assert "门禁覆盖" in crm.page.get_by_test_id("stage-history").inner_text()


def test_sales_cannot_see_override_field(as_sales_crm):
    """销售没有覆盖权限，界面上就不该出现覆盖理由输入框。"""
    crm = _open_project_form(as_sales_crm)

    assert crm.page.get_by_test_id("advance-override").count() == 0


def test_project_filter_by_stage(as_sales_crm):
    crm = as_sales_crm
    crm.goto("/projects")

    crm.page.get_by_test_id("stage-filter-opportunity").click()
    crm.page.wait_for_url("**/projects?stage=opportunity")

    assert "stage=opportunity" in crm.page.url


# ==========================================================================
# 越权（E2E 视角）
# ==========================================================================
def test_sales_cannot_open_peer_customer_by_url(crm):
    """销售 A 拿到客户详情 URL 后交给销售 B，B 应被挡回列表。"""
    crm.login("sales_a")
    crm.goto("/customers")
    href = crm.page.get_by_test_id("customer-link").first.get_attribute("href")
    assert href and href.startswith("/customers/")

    crm.logout()
    crm.login("sales_b")
    crm.goto(href)

    assert crm.path == "/customers"
    assert "无权查看" in crm.flash


def test_marketing_role_sees_readonly_customers(as_marketing_crm):
    crm = as_marketing_crm
    crm.goto("/customers")

    assert crm.page.get_by_test_id("customer-table").count() == 1
    assert crm.page.get_by_test_id("customer-form").count() == 0


# ==========================================================================
# 看板
# ==========================================================================
def test_dashboard_shows_metrics(as_sales_crm):
    crm = as_sales_crm
    crm.goto("/")

    for test_id in ["metric-visits", "metric-abnormal", "metric-projects", "metric-revenue"]:
        assert crm.page.get_by_test_id(test_id).count() == 1, f"看板缺少 {test_id}"
