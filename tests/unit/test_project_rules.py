"""销售项目金额与阶段停留计算（需求文档 2.2 偏离三、R-26）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.services.project import check_amount_change, compute_revenue, evaluate_stay_days

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
