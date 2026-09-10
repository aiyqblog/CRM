"""拜访接口测试 —— 逐条覆盖需求文档 R-01 ~ R-13。

断言一律基于错误响应的 ``code``（规则编号），不匹配中文文案：
文案会随措辞优化而变，规则编号是契约。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import VisitRecord
from tests.conftest import (
    FAR_LAT,
    FAR_LNG,
    LONG_CONTENT,
    NEARBY_LAT,
    NEARBY_LNG,
)
from tests.helpers import build_gps_jpeg

pytestmark = pytest.mark.api


def do_checkin(client, customer_id, **overrides):
    payload = {
        "customer_id": customer_id,
        "longitude": NEARBY_LNG,
        "latitude": NEARBY_LAT,
        "address": "深圳市南山区科技园南区高新南七道 12 号",
    }
    payload.update(overrides)
    return client.post("/api/visits/checkin", json=payload)


def do_checkout(client, record_id, **overrides):
    payload = {"content": LONG_CONTENT}
    payload.update(overrides)
    return client.post(f"/api/visits/{record_id}/checkout", json=payload)


def make_plan(client, customer_id, *, start_offset_minutes: int = 0):
    start = datetime.utcnow() + timedelta(minutes=start_offset_minutes)
    return client.post(
        "/api/visit-plans",
        json={
            "customer_id": customer_id,
            "plan_start": start.isoformat(),
            "plan_end": (start + timedelta(hours=2)).isoformat(),
            "visit_purpose": "例行技术交流",
            "visit_type": 2,
        },
    )


# ==========================================================================
# 签到
# ==========================================================================
def test_checkin_success(as_sales, customer_id):
    response = do_checkin(as_sales, customer_id)
    assert response.status_code == 201
    body = response.json()
    assert body["record_no"].startswith("VSR")
    assert body["location_status"] == 1
    assert body["distance_m"] < 300
    assert body["status"] == 2
    assert body["abnormal_flag"] == 0


def test_checkin_requires_login(client, customer_id):
    assert do_checkin(client, customer_id).status_code == 403


def test_checkin_out_of_fence_flagged_but_allowed(as_sales, customer_id):
    """R-03：超出围栏不阻断，只标记。"""
    response = do_checkin(
        as_sales, customer_id, longitude=FAR_LNG, latitude=FAR_LAT
    )
    assert response.status_code == 201
    body = response.json()
    assert body["location_status"] == 2
    assert body["distance_m"] > 1000


def test_checkin_mocked_location_marks_abnormal(as_sales, customer_id):
    """R-07：模拟定位标记异常并强制复核。"""
    response = do_checkin(as_sales, customer_id, is_mocked=True)
    assert response.status_code == 201
    body = response.json()
    assert body["location_status"] == 4
    assert body["abnormal_flag"] == 1


def test_checkin_overlap_rejected(as_sales, customer_id):
    """R-04：同一时段不允许重叠的未签退记录。"""
    assert do_checkin(as_sales, customer_id).status_code == 201

    second = do_checkin(as_sales, customer_id)
    assert second.status_code == 409
    assert second.json()["code"] == "R-04"


def test_overlap_check_is_per_user(as_sales, as_manager, customer_id):
    """重叠判定只针对同一拜访人：主管在同一客户处签到不应冲突。

    这里不能用同组另一名销售作对照 —— 那会先撞上客户的数据权限（403），
    根本走不到重叠判定。主管对本团队客户有可见性，才能验证到 R-04 的粒度。
    """
    assert do_checkin(as_sales, customer_id).status_code == 201
    assert do_checkin(as_manager, customer_id).status_code == 201


def test_checkin_other_team_customer_rejected(as_sales_other_team, customer_id):
    """越权：不能给不属于自己可见范围的客户签到。"""
    response = do_checkin(as_sales_other_team, customer_id)
    assert response.status_code == 403


def test_checkin_plan_too_early_rejected(as_sales, customer_id):
    """R-01：签到不得早于计划开始时间前 30 分钟。"""
    plan = make_plan(as_sales, customer_id, start_offset_minutes=120)
    assert plan.status_code == 201
    plan_id = plan.json()["id"]

    response = do_checkin(as_sales, customer_id, plan_id=plan_id)
    assert response.status_code == 400
    assert response.json()["code"] == "R-01"


def test_checkin_plan_within_tolerance_allowed(as_sales, customer_id):
    """计划 20 分钟后开始，现在签到应放行（在 30 分钟容差内）。"""
    plan = make_plan(as_sales, customer_id, start_offset_minutes=20)
    plan_id = plan.json()["id"]

    response = do_checkin(as_sales, customer_id, plan_id=plan_id)
    assert response.status_code == 201


def test_checkin_cancelled_plan_rejected(as_sales, customer_id):
    plan = make_plan(as_sales, customer_id, start_offset_minutes=-10)
    plan_id = plan.json()["id"]
    as_sales.post(f"/api/visit-plans/{plan_id}/cancel", json={"reason": "客户临时取消"})

    response = do_checkin(as_sales, customer_id, plan_id=plan_id)
    assert response.status_code == 400
    assert response.json()["code"] == "PLAN-01"


def test_checkin_missing_customer(as_sales):
    response = do_checkin(as_sales, 999999)
    assert response.status_code == 404


# ==========================================================================
# 签退
# ==========================================================================
def test_checkout_success(as_sales, customer_id):
    record = do_checkin(as_sales, customer_id).json()
    response = do_checkout(as_sales, record["id"])
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 3
    assert body["duration_min"] is not None
    assert body["content"] == LONG_CONTENT


def test_checkout_short_content_rejected(as_sales, customer_id):
    """R-11：纪要至少 20 字。"""
    record = do_checkin(as_sales, customer_id).json()
    response = do_checkout(as_sales, record["id"], content="太短了")
    assert response.status_code == 400
    assert response.json()["code"] == "R-11"


def test_checkout_before_checkin_rejected(as_sales, customer_id):
    """R-02：签退时间必须晚于签到时间。"""
    record = do_checkin(as_sales, customer_id).json()
    earlier = datetime.fromisoformat(record["checkin_time"]) - timedelta(hours=1)
    response = do_checkout(as_sales, record["id"], checkout_time=earlier.isoformat())
    assert response.status_code == 400
    assert response.json()["code"] == "R-02"


def test_checkout_short_duration_marks_abnormal(as_sales, customer_id):
    """R-05：时长过短标记异常但不阻断。"""
    record = do_checkin(as_sales, customer_id).json()
    response = do_checkout(as_sales, record["id"])
    assert response.status_code == 200
    body = response.json()
    assert body["abnormal_flag"] == 1
    assert body["abnormal_reason"]


def test_checkout_out_of_fence_requires_note(as_sales, customer_id):
    """R-03：超出围栏时必须填写说明。"""
    record = do_checkin(
        as_sales, customer_id, longitude=FAR_LNG, latitude=FAR_LAT
    ).json()
    assert record["location_status"] == 2

    without_note = do_checkout(as_sales, record["id"])
    assert without_note.status_code == 400
    assert without_note.json()["code"] == "R-03"

    with_note = do_checkout(
        as_sales, record["id"], location_note="客户临时改到分公司会议室"
    )
    assert with_note.status_code == 200


def test_checkout_twice_rejected(as_sales, customer_id):
    record = do_checkin(as_sales, customer_id).json()
    assert do_checkout(as_sales, record["id"]).status_code == 200
    second = do_checkout(as_sales, record["id"])
    assert second.status_code == 400
    assert second.json()["code"] == "STATE-01"


def test_checkout_by_other_user_rejected(
    as_sales, as_sales_other_team, customer_id
):
    record = do_checkin(as_sales, customer_id).json()
    response = do_checkout(as_sales_other_team, record["id"])
    assert response.status_code == 403


# ==========================================================================
# 删除
# ==========================================================================
def test_delete_within_window(as_sales, customer_id):
    record = do_checkin(as_sales, customer_id).json()
    response = as_sales.delete(f"/api/visits/{record['id']}")
    assert response.status_code == 200
    assert as_sales.get(f"/api/visits/{record['id']}").status_code == 404


def test_delete_beyond_window_rejected(as_sales, customer_id, db):
    """R-12：超过 24 小时只能走主管流程。"""
    record_id = do_checkin(as_sales, customer_id).json()["id"]

    row = db.get(VisitRecord, record_id)
    row.checkin_time = datetime.utcnow() - timedelta(hours=30)
    db.commit()

    response = as_sales.delete(f"/api/visits/{record_id}")
    assert response.status_code == 403
    assert response.json()["code"] == "R-12"


def test_delete_linked_to_project_rejected(as_sales, customer_id):
    """R-13：已关联项目的拜访记录不可删除。"""
    project = as_sales.post(
        "/api/projects",
        json={"project_name": "联动测试项目", "customer_id": customer_id},
    ).json()

    record = do_checkin(as_sales, customer_id, project_id=project["id"]).json()
    response = as_sales.delete(f"/api/visits/{record['id']}")
    assert response.status_code == 403
    assert response.json()["code"] == "R-13"


def test_delete_by_other_user_rejected(as_sales, as_sales_other_team, customer_id):
    record = do_checkin(as_sales, customer_id).json()
    response = as_sales_other_team.delete(f"/api/visits/{record['id']}")
    assert response.status_code == 403


# ==========================================================================
# 列表、异常队列、离线补传
# ==========================================================================
def test_list_visits_scoped_to_user(as_sales, customer_id):
    do_checkin(as_sales, customer_id)
    response = as_sales.get("/api/visits")
    assert response.status_code == 200
    assert response.json()["total"] >= 1


def test_list_visits_by_customer(as_sales, customer_id):
    do_checkin(as_sales, customer_id)
    response = as_sales.get("/api/visits", params={"customer_id": customer_id})
    assert response.json()["total"] >= 1


def test_list_visits_by_type(as_sales, customer_id):
    do_checkin(as_sales, customer_id, visit_type=3)
    response = as_sales.get("/api/visits", params={"visit_type": 3})
    assert response.status_code == 200
    assert response.json()["total"] >= 1


def test_abnormal_queue_contains_flagged(as_sales, customer_id):
    do_checkin(as_sales, customer_id, is_mocked=True)
    response = as_sales.get("/api/visits/abnormal")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert all(item["abnormal_flag"] == 1 for item in body["items"])


def test_offline_checkin_then_sync(as_sales, customer_id):
    """R-10 路径：离线签到 → 待同步 → 补传。"""
    client_time = datetime.utcnow() - timedelta(hours=5)
    record = do_checkin(
        as_sales, customer_id, offline=True, client_time=client_time.isoformat()
    ).json()
    assert record["sync_status"] == 2
    assert record["abnormal_flag"] == 1
    assert "时间偏差" in record["abnormal_reason"]

    response = as_sales.post("/api/visits/sync", json={"record_ids": [record["id"]]})
    assert response.status_code == 200
    assert response.json()["synced"] == 1

    refreshed = as_sales.get(f"/api/visits/{record['id']}").json()
    assert refreshed["sync_status"] == 1


def test_offline_sync_cannot_touch_others_records(
    as_sales, as_sales_other_team, customer_id
):
    record = do_checkin(as_sales, customer_id, offline=True).json()
    response = as_sales_other_team.post(
        "/api/visits/sync", json={"record_ids": [record["id"]]}
    )
    assert response.json()["synced"] == 0


# ==========================================================================
# 附件
# ==========================================================================
def test_upload_image_extracts_exif_gps(as_sales, customer_id):
    """核心防作弊用例：坐标必须由服务端从 EXIF 读出，而非前端自报。"""
    record = do_checkin(as_sales, customer_id).json()
    image = build_gps_jpeg(latitude=22.5250, longitude=113.9430)

    response = as_sales.post(
        f"/api/visits/{record['id']}/attachments",
        files={"file": ("photo.jpg", image, "image/jpeg")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["exif_gps_found"] is True
    assert body["capture_lat"] == pytest.approx(22.5250, abs=1e-4)
    assert body["capture_lng"] == pytest.approx(113.9430, abs=1e-4)
    assert body["captured_at"] is not None
    assert body["watermark"]
    assert "download_url" in body


def test_upload_image_without_exif_still_accepted(as_sales, customer_id):
    """相册转存会丢 EXIF —— 这是常态，不能因此拒绝上传。"""
    from tests.helpers import build_plain_jpeg

    record = do_checkin(as_sales, customer_id).json()
    response = as_sales.post(
        f"/api/visits/{record['id']}/attachments",
        files={"file": ("plain.jpg", build_plain_jpeg(), "image/jpeg")},
    )
    assert response.status_code == 201
    assert response.json()["exif_gps_found"] is False


def test_upload_oversized_file_rejected(as_sales, customer_id, monkeypatch):
    """R-08：单文件超过 20MB 拒绝。"""
    from app.config import settings

    monkeypatch.setattr(settings, "max_upload_bytes", 100)

    record = do_checkin(as_sales, customer_id).json()
    response = as_sales.post(
        f"/api/visits/{record['id']}/attachments",
        files={"file": ("big.jpg", b"x" * 200, "image/jpeg")},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "R-08"


def test_upload_unsupported_extension_rejected(as_sales, customer_id):
    record = do_checkin(as_sales, customer_id).json()
    response = as_sales.post(
        f"/api/visits/{record['id']}/attachments",
        files={"file": ("evil.exe", b"binary", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "R-08"


def test_image_count_limit_enforced(as_sales, customer_id):
    """R-08：单条记录图片上限。"""
    record = do_checkin(as_sales, customer_id).json()
    image = build_gps_jpeg()

    for index in range(9):
        response = as_sales.post(
            f"/api/visits/{record['id']}/attachments",
            files={"file": (f"p{index}.jpg", image, "image/jpeg")},
        )
        assert response.status_code == 201, f"第 {index + 1} 张应被接受"

    tenth = as_sales.post(
        f"/api/visits/{record['id']}/attachments",
        files={"file": ("p10.jpg", image, "image/jpeg")},
    )
    assert tenth.status_code == 400
    assert tenth.json()["code"] == "R-08"


def test_upload_to_others_record_rejected(as_sales, as_sales_other_team, customer_id):
    record = do_checkin(as_sales, customer_id).json()
    response = as_sales_other_team.post(
        f"/api/visits/{record['id']}/attachments",
        files={"file": ("p.jpg", build_gps_jpeg(), "image/jpeg")},
    )
    assert response.status_code == 403


def test_download_requires_valid_signature(as_sales, customer_id):
    record = do_checkin(as_sales, customer_id).json()
    upload = as_sales.post(
        f"/api/visits/{record['id']}/attachments",
        files={"file": ("p.jpg", build_gps_jpeg(), "image/jpeg")},
    ).json()

    # 签名链接必须指向真实存在的下载路由
    assert upload["download_url"].startswith("/api/files/")
    assert as_sales.get(upload["download_url"]).status_code == 200

    base = upload["download_url"].split("?")[0]
    # 篡改签名
    forged = as_sales.get(f"{base}?e=9999999999&s=deadbeef")
    assert forged.status_code == 403
    # 已过期
    expired = as_sales.get(f"{base}?e=1&s=deadbeef")
    assert expired.status_code == 403


def test_download_requires_login(client, as_sales, customer_id):
    """签名合法但未登录，同样不放行。"""
    record = do_checkin(as_sales, customer_id).json()
    upload = as_sales.post(
        f"/api/visits/{record['id']}/attachments",
        files={"file": ("p.jpg", build_gps_jpeg(), "image/jpeg")},
    ).json()

    response = client.get(upload["download_url"])
    assert response.status_code == 403


# ==========================================================================
# 拜访计划
# ==========================================================================
def test_create_plan(as_sales, customer_id):
    response = make_plan(as_sales, customer_id, start_offset_minutes=60)
    assert response.status_code == 201
    body = response.json()
    assert body["plan_no"].startswith("VST")
    assert body["status"] == 1


def test_plan_end_before_start_rejected(as_sales, customer_id):
    start = datetime.utcnow() + timedelta(hours=2)
    response = as_sales.post(
        "/api/visit-plans",
        json={
            "customer_id": customer_id,
            "plan_start": start.isoformat(),
            "plan_end": (start - timedelta(hours=1)).isoformat(),
            "visit_purpose": "时间倒置",
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "PLAN-03"


def test_cancel_plan_requires_reason(as_sales, customer_id):
    plan = make_plan(as_sales, customer_id).json()
    response = as_sales.post(f"/api/visit-plans/{plan['id']}/cancel", json={"reason": ""})
    assert response.status_code == 400
    assert response.json()["code"] == "PLAN-04"


def test_cancel_plan_ok(as_sales, customer_id):
    plan = make_plan(as_sales, customer_id).json()
    response = as_sales.post(
        f"/api/visit-plans/{plan['id']}/cancel", json={"reason": "客户临时有事"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == 4


def test_checkin_completes_plan(as_sales, customer_id):
    """签到 → 计划转进行中；签退 → 计划转已完成。"""
    plan = make_plan(as_sales, customer_id, start_offset_minutes=-10).json()
    record = do_checkin(as_sales, customer_id, plan_id=plan["id"]).json()

    plans = as_sales.get("/api/visit-plans", params={"customer_id": customer_id})
    current = next(p for p in plans.json()["items"] if p["id"] == plan["id"])
    assert current["status"] == 2

    do_checkout(as_sales, record["id"])
    plans = as_sales.get("/api/visit-plans", params={"customer_id": customer_id})
    current = next(p for p in plans.json()["items"] if p["id"] == plan["id"])
    assert current["status"] == 3


def test_auto_close_stale_records(as_sales, customer_id, db):
    """状态机：超时未签退自动关闭并标记异常。"""
    from app.services.visit import auto_close_stale

    record_id = do_checkin(as_sales, customer_id).json()["id"]
    row = db.get(VisitRecord, record_id)
    row.checkin_time = datetime.utcnow() - timedelta(hours=30)
    db.commit()

    closed = auto_close_stale(db)
    db.commit()
    assert closed == 1

    refreshed = as_sales.get(f"/api/visits/{record_id}").json()
    assert refreshed["status"] == 5
    assert refreshed["abnormal_flag"] == 1


# ==========================================================================
# 按客户名搜索
# ==========================================================================
def search_visits(client, keyword: str, **params):
    query = {"customer_keyword": keyword}
    query.update(params)
    return client.get("/api/visits", params=query)


def test_search_by_customer_name_hits(as_sales, customer_id):
    do_checkin(as_sales, customer_id)
    body = search_visits(as_sales, "深圳").json()
    assert body["total"] == 1
    assert body["items"][0]["customer_id"] == customer_id


def test_search_by_customer_name_misses(as_sales, customer_id):
    do_checkin(as_sales, customer_id)
    body = search_visits(as_sales, "完全不存在的客户").json()
    assert body["total"] == 0
    assert body["items"] == []


def test_search_with_empty_keyword_returns_all(as_sales, customer_id):
    """空关键词等同不筛选 —— 清空搜索框不该把列表清成空的。"""
    do_checkin(as_sales, customer_id)
    assert search_visits(as_sales, "").json()["total"] == 1


def test_search_keyword_only_spaces_returns_all(as_sales, customer_id):
    do_checkin(as_sales, customer_id)
    assert search_visits(as_sales, "     ").json()["total"] == 1


def test_search_keyword_is_trimmed(as_sales, customer_id):
    do_checkin(as_sales, customer_id)
    assert search_visits(as_sales, "  深圳  ").json()["total"] == 1


def test_search_wildcard_chars_treated_as_literals(as_sales, customer_id):
    """``%`` / ``_`` 必须当字面量：否则搜 ``%`` 会命中全部记录，像筛选没生效。"""
    do_checkin(as_sales, customer_id)
    assert search_visits(as_sales, "%").json()["total"] == 0
    assert search_visits(as_sales, "_").json()["total"] == 0


def test_search_composes_with_visit_type(as_sales, customer_id):
    """关键词与已有筛选条件叠加，而不是相互覆盖。"""
    do_checkin(as_sales, customer_id)  # 未指定类型时默认「例行拜访」(2)
    assert search_visits(as_sales, "深圳", visit_type=2).json()["total"] == 1
    assert search_visits(as_sales, "深圳", visit_type=3).json()["total"] == 0


def test_search_does_not_bypass_row_level_permission(
    as_sales, as_sales_other_team, customer_id
):
    """越权测试：他组销售用同样的关键词，搜不到不属于他的记录。

    搜索必须叠加在数据权限之上，而不是绕开它 —— 这是本功能最容易出的安全问题。
    """
    do_checkin(as_sales, customer_id)
    assert search_visits(as_sales, "深圳").json()["total"] == 1
    assert search_visits(as_sales_other_team, "深圳").json()["total"] == 0
