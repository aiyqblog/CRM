"""地理计算与定位状态判定（R-03 / R-06 / R-07）。

纯函数，不依赖数据库，是单元测试的优先目标。
"""

from __future__ import annotations

import math

from app.constants import LocationStatus

#: 地球平均半径（米）
EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点间大圆距离（米）。

    销售拜访的围栏判定精度要求是百米级，Haversine 完全够用；
    不需要 Vincenty 那种椭球模型。
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def evaluate_location(
    *,
    customer_lat: float | None,
    customer_lng: float | None,
    checkin_lat: float,
    checkin_lng: float,
    fence_meters: int,
    is_mocked: bool = False,
) -> tuple[LocationStatus, int | None]:
    """判定签到定位状态。

    返回 ``(状态, 距离米)``。距离为 ``None`` 表示无法计算。

    规则优先级：模拟定位 > 客户无坐标 > 围栏判定。
    模拟定位优先是因为它比围栏更有指示性——伪造位置的人通常也不会在现场。
    """
    if is_mocked:
        # R-07：检测到模拟定位，标记并强制主管复核。不阻断提交，
        # 因为真机上偶发误判也会触发，阻断会造成大量真实拜访录不进去。
        return LocationStatus.MOCKED, None

    if customer_lat is None or customer_lng is None:
        # R-06：客户地址缺失，跳过距离校验
        return LocationStatus.FAILED, None

    distance = haversine_m(
        float(customer_lat), float(customer_lng), float(checkin_lat), float(checkin_lng)
    )
    rounded = int(round(distance))

    if distance > fence_meters:
        # R-03：超出范围不阻断，但强制填写说明
        return LocationStatus.OUT_OF_FENCE, rounded

    return LocationStatus.NORMAL, rounded
