"""销售项目金额与阶段停留计算（需求文档 2.2 偏离三、R-26）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.constants import PRODUCT_SERIES_MODELS, ProjectCategory
from app.services.errors import ValidationFailed
from app.services.project import (
    check_amount_change,
    check_product_selection,
    check_project_category,
    compute_revenue,
    evaluate_stay_days,
)

pytestmark = pytest.mark.unit


class TestComputeRevenue:
    """双维度金额口径：单价 × 年用量 = 年营收；年营收 × 生命周期 = 总营收。"""

    def test_full_calculation(self):
        annual, total = compute_revenue(12.5, 200_000, 3)
        assert annual == Decimal("2500000.00")
        assert total == Decimal("7500000.00")

    def test_missing_qty_returns_both_none(self):
        """缺任一项都返回 None —— 半算出来的金额比没有金额更危险。"""
        annual, total = compute_revenue(12.5, None, 3)
        assert annual is None and total is None

    def test_missing_price_returns_both_none(self):
        annual, total = compute_revenue(None, 100_000, 3)
        assert annual is None and total is None

    def test_missing_lifecycle_gives_annual_only(self):
        """生命周期可缺省，此时仍应给出年营收。"""
        annual, total = compute_revenue(10, 1000, None)
        assert annual == Decimal("10000.00")
        assert total is None

    def test_zero_qty_is_valid(self):
        annual, total = compute_revenue(10, 0, 3)
        assert annual == Decimal("0.00")
        assert total == Decimal("0.00")

    def test_decimal_input_precision(self):
        """金额必须走 Decimal，不能有浮点误差。"""
        annual, _ = compute_revenue("0.1", 3, None)
        assert annual == Decimal("0.30")

    def test_float_input_converted_cleanly(self):
        annual, _ = compute_revenue(0.07, 100, None)
        assert annual == Decimal("7.00")


class TestAmountChange:
    """R-26：金额变更超过阈值需审批。"""

    def test_within_threshold(self):
        exceeded, ratio = check_amount_change(1_000_000, 1_100_000, 0.20)
        assert exceeded is False
        assert ratio == pytest.approx(0.10)

    def test_exactly_at_threshold_not_exceeded(self):
        """边界值：恰好 20% 不算超阈值（用严格大于）。"""
        exceeded, ratio = check_amount_change(1_000_000, 1_200_000, 0.20)
        assert exceeded is False
        assert ratio == pytest.approx(0.20)

    def test_beyond_threshold_exceeded(self):
        exceeded, ratio = check_amount_change(1_000_000, 1_200_001, 0.20)
        assert exceeded is True
        assert ratio > 0.20

    def test_decrease_also_detected(self):
        """金额大幅下调同样需要审批。"""
        exceeded, ratio = check_amount_change(1_000_000, 500_000, 0.20)
        assert exceeded is True
        assert ratio == pytest.approx(0.50)

    def test_first_entry_never_exceeds(self):
        """旧值为空表示首次录入，不算变更。"""
        exceeded, ratio = check_amount_change(None, 999, 0.20)
        assert exceeded is False and ratio == 0.0

    def test_new_value_none_never_exceeds(self):
        exceeded, _ = check_amount_change(1_000_000, None, 0.20)
        assert exceeded is False

    def test_old_zero_no_division_error(self):
        """旧值为 0 时不能抛除零异常。"""
        exceeded, ratio = check_amount_change(0, 500_000, 0.20)
        assert exceeded is False and ratio == 0.0

    def test_float_and_decimal_mixed(self):
        exceeded, _ = check_amount_change(Decimal("1000.00"), 1300.0, 0.20)
        assert exceeded is True


class TestStayDays:
    def test_same_day_is_zero(self):
        now = datetime(2026, 9, 10, 10, 0)
        assert evaluate_stay_days(now, now + timedelta(hours=5)) == 0

    def test_counts_natural_days(self):
        start = datetime(2026, 9, 1, 9, 0)
        now = datetime(2026, 9, 11, 9, 0)
        assert evaluate_stay_days(start, now) == 10

    def test_never_negative(self):
        """时钟回拨等异常情况下不能出现负数停留。"""
        start = datetime(2026, 9, 10, 10, 0)
        now = datetime(2026, 9, 9, 10, 0)
        assert evaluate_stay_days(start, now) == 0


class TestCheckProjectCategory:
    """R-31：项目类别取值（Issue #5）。"""

    def test_empty_is_allowed(self):
        """允许不填 —— 老客户端与历史数据都没有这个字段。"""
        check_project_category(None)

    @pytest.mark.parametrize("value", [1, 2])
    def test_known_categories_pass(self, value):
        check_project_category(value)

    @pytest.mark.parametrize("value", [0, 3, 99, -1])
    def test_unknown_category_rejected(self, value):
        with pytest.raises(ValidationFailed) as exc:
            check_project_category(value)
        assert exc.value.code == "R-31"

    def test_enum_members_are_accepted(self):
        """传枚举成员本身也该通过 —— 混入枚举的 hash 等于裸值。"""
        check_project_category(ProjectCategory.LARGE)
        check_project_category(ProjectCategory.SMALL)


class TestCheckProductSelection:
    """R-31：产品型号必须隶属所选产品系列（Issue #5）。

    页面上有联动下拉，但前端过滤只是体验：直接调接口、改 DOM 都能绕过。
    这些用例守的是服务端那一层。
    """

    def test_both_empty_is_allowed(self):
        check_product_selection(None, None)
        check_product_selection("", "")

    def test_series_without_model_is_allowed(self):
        """只选系列不选型号是合法中间态 —— 型号允许稍后再定。"""
        check_product_selection("卫星通信天线", None)
        check_product_selection("卫星通信天线", "")

    @pytest.mark.parametrize(
        ("series", "model"),
        [
            (series, model)
            for series, models in PRODUCT_SERIES_MODELS.items()
            for model in models
        ],
    )
    def test_every_configured_model_is_accepted(self, series, model):
        """清单里的每个组合都必须通过 —— 防止清单与校验逻辑写岔。"""
        check_product_selection(series, model)

    def test_model_without_series_is_rejected(self):
        """没有系列就无从判断归属，宁可报错也不要放一个来历不明的型号进库。"""
        with pytest.raises(ValidationFailed) as exc:
            check_product_selection(None, "YECT005W1A")
        assert exc.value.code == "R-31"
        assert "先选择产品系列" in exc.value.message

    def test_cross_series_model_is_rejected(self):
        """真实的坏组合：卫星通信天线的项目挂了蜂窝天线的型号。"""
        with pytest.raises(ValidationFailed) as exc:
            check_product_selection("卫星通信天线", "YECT005W1A")
        assert exc.value.code == "R-31"
        assert exc.value.details["allowed_models"] == list(PRODUCT_SERIES_MODELS["卫星通信天线"])

    def test_unknown_series_is_rejected(self):
        with pytest.raises(ValidationFailed) as exc:
            check_product_selection("量子天线", None)
        assert exc.value.code == "R-31"

    def test_unknown_model_in_known_series_is_rejected(self):
        with pytest.raises(ValidationFailed) as exc:
            check_product_selection("GNSS定位天线", "YECT005W1A")
        assert exc.value.code == "R-31"

    @pytest.mark.parametrize("series", list(PRODUCT_SERIES_MODELS))
    def test_series_names_are_non_empty(self, series):
        """清单完整性：不允许出现空系列或空型号，否则下拉框会渲染出空选项。"""
        assert series.strip() == series
        assert PRODUCT_SERIES_MODELS[series], f"{series} 没有配置任何型号"

    def test_whitespace_is_trimmed(self):
        """表单值两侧带空格时不该被判成未知系列。"""
        check_product_selection(" 卫星通信天线 ", " YFTA009E3AM ")

    def test_model_codes_have_no_invisible_characters(self):
        """从需求文档复制型号时极易带入零宽字符，肉眼完全看不出来。"""
        import unicodedata

        for series, models in PRODUCT_SERIES_MODELS.items():
            for model in models:
                assert model == unicodedata.normalize("NFKC", model)
                assert not any(ord(ch) > 127 for ch in model), f"{series}/{model} 含非 ASCII 字符"
