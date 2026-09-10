"""地理计算与定位状态判定（R-03 / R-06 / R-07）。"""

from __future__ import annotations

import pytest

from app.constants import LocationStatus
from app.services.geo import evaluate_location, haversine_m

pytestmark = pytest.mark.unit

# 深圳南山科技园
CUSTOMER_LNG, CUSTOMER_LAT = 113.9432, 22.5248


class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_m(22.5, 113.9, 22.5, 113.9) == pytest.approx(0, abs=1e-6)

    def test_shenzhen_to_guangzhou(self):
        """深圳到广州直线约 105 公里，允许 3 公里误差。"""
        distance = haversine_m(22.54, 114.06, 23.13, 113.26)
        assert 102_000 < distance < 108_000

    def test_one_degree_latitude_is_about_111km(self):
        assert haversine_m(0.0, 0.0, 1.0, 0.0) == pytest.approx(111_195, rel=0.01)

    def test_symmetry(self):
        a = haversine_m(22.5, 113.9, 23.1, 113.3)
        b = haversine_m(23.1, 113.3, 22.5, 113.9)
        assert a == pytest.approx(b, rel=1e-9)


class TestEvaluateLocation:
    def test_normal_within_fence(self):
        status, distance = evaluate_location(
            customer_lat=CUSTOMER_LAT,
            customer_lng=CUSTOMER_LNG,
            checkin_lat=22.5252,
            checkin_lng=113.9445,
            fence_meters=1000,
        )
        assert status is LocationStatus.NORMAL
        assert distance is not None and distance < 300

    def test_out_of_fence(self):
        """R-03：超出围栏只标记，不阻断。"""
        status, distance = evaluate_location(
            customer_lat=CUSTOMER_LAT,
            customer_lng=CUSTOMER_LNG,
            checkin_lat=22.5600,
            checkin_lng=113.9900,
            fence_meters=1000,
        )
        assert status is LocationStatus.OUT_OF_FENCE
        assert distance > 1000

    def test_exactly_on_fence_boundary_is_normal(self):
        """边界值：距离恰好等于围栏半径时算正常（不越界）。"""
        # 在客户正北方向取一个精确距离的点较难，这里改为验证 <= 判定语义
        status, distance = evaluate_location(
            customer_lat=CUSTOMER_LAT,
            customer_lng=CUSTOMER_LNG,
            checkin_lat=CUSTOMER_LAT,
            checkin_lng=CUSTOMER_LNG,
            fence_meters=0,
        )
        assert distance == 0
        assert status is LocationStatus.NORMAL

    def test_mocked_overrides_everything(self):
        """R-07：模拟定位优先级最高，即使就在客户位置上。"""
        status, distance = evaluate_location(
            customer_lat=CUSTOMER_LAT,
            customer_lng=CUSTOMER_LNG,
            checkin_lat=CUSTOMER_LAT,
            checkin_lng=CUSTOMER_LNG,
            fence_meters=1000,
            is_mocked=True,
        )
        assert status is LocationStatus.MOCKED
        assert distance is None

    def test_customer_without_coords_skips_distance(self):
        """R-06：客户无坐标时跳过距离校验。"""
        status, distance = evaluate_location(
            customer_lat=None,
            customer_lng=None,
            checkin_lat=22.5252,
            checkin_lng=113.9445,
            fence_meters=1000,
        )
        assert status is LocationStatus.FAILED
        assert distance is None

    def test_partial_coords_treated_as_missing(self):
        """只填了纬度没填经度，同样按缺坐标处理。"""
        status, _ = evaluate_location(
            customer_lat=CUSTOMER_LAT,
            customer_lng=None,
            checkin_lat=22.5252,
            checkin_lng=113.9445,
            fence_meters=1000,
        )
        assert status is LocationStatus.FAILED

    def test_string_coords_are_coerced(self):
        """ORM 的 Numeric 可能返回 Decimal，必须能兼容。"""
        from decimal import Decimal

        status, distance = evaluate_location(
            customer_lat=Decimal("22.5248"),
            customer_lng=Decimal("113.9432"),
            checkin_lat=22.5252,
            checkin_lng=113.9445,
            fence_meters=1000,
        )
        assert status is LocationStatus.NORMAL
        assert distance is not None
