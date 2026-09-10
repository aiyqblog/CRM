"""拜访规则纯函数单元测试 —— 覆盖需求文档 R-01 ~ R-11。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.constants import FileType, LocationStatus
from app.services.errors import ValidationFailed
from app.services.visit import (
    check_attachment_limits,
    check_checkin_time,
    check_checkout_order,
    check_content,
    check_location_note,
    compute_duration_min,
    evaluate_duration,
    evaluate_mocked_location,
    evaluate_offline_drift,
)

pytestmark = pytest.mark.unit

BASE = datetime(2026, 9, 10, 10, 0, 0)


class TestCheckinTime:
    """R-01：签到不得早于计划开始时间前 30 分钟。"""

    def test_on_time(self):
        check_checkin_time(BASE, BASE, 30)

    def test_within_tolerance_before(self):
        check_checkin_time(BASE, BASE - timedelta(minutes=29), 30)

    def test_exactly_at_tolerance_boundary(self):
        """边界值：恰好提前 30 分钟应放行。"""
        check_checkin_time(BASE, BASE - timedelta(minutes=30), 30)

    def test_too_early_raises(self):
        with pytest.raises(ValidationFailed) as exc:
            check_checkin_time(BASE, BASE - timedelta(minutes=31), 30)
        assert exc.value.code == "R-01"

    def test_after_plan_start_always_ok(self):
        check_checkin_time(BASE, BASE + timedelta(hours=5), 30)

    def test_no_plan_skips_check(self):
        """临时拜访没有计划，跳过该校验。"""
        check_checkin_time(None, BASE - timedelta(days=1), 30)


class TestCheckoutOrder:
    """R-02：签退必须晚于签到。"""

    def test_valid(self):
        check_checkout_order(BASE, BASE + timedelta(hours=1))

    def test_equal_raises(self):
        with pytest.raises(ValidationFailed) as exc:
            check_checkout_order(BASE, BASE)
        assert exc.value.code == "R-02"

    def test_earlier_raises(self):
        with pytest.raises(ValidationFailed) as exc:
            check_checkout_order(BASE, BASE - timedelta(minutes=1))
        assert exc.value.code == "R-02"


class TestDuration:
    def test_compute_truncates_to_minute(self):
        assert compute_duration_min(BASE, BASE + timedelta(minutes=45, seconds=59)) == 45

    def test_zero_duration(self):
        assert compute_duration_min(BASE, BASE) == 0

    def test_normal_not_abnormal(self):
        abnormal, reason = evaluate_duration(BASE, BASE + timedelta(minutes=30), 5)
        assert abnormal is False and reason is None

    def test_too_short_is_abnormal(self):
        """R-05：不阻断，但标记异常。"""
        abnormal, reason = evaluate_duration(BASE, BASE + timedelta(minutes=2), 5)
        assert abnormal is True and reason

    def test_exactly_at_threshold_is_ok(self):
        """边界值：恰好 5 分钟不算异常。"""
        abnormal, _ = evaluate_duration(BASE, BASE + timedelta(minutes=5), 5)
        assert abnormal is False


class TestContent:
    """R-11：纪要字数下限。"""

    def test_long_enough(self):
        check_content("这是一段足够长的拜访纪要内容用于通过校验", 20)

    def test_too_short_raises(self):
        with pytest.raises(ValidationFailed) as exc:
            check_content("太短", 20)
        assert exc.value.code == "R-11"

    def test_none_raises(self):
        with pytest.raises(ValidationFailed):
            check_content(None, 20)

    def test_whitespace_does_not_count(self):
        """只填空格不能算字数。"""
        with pytest.raises(ValidationFailed) as exc:
            check_content(" " * 50, 20)
        assert exc.value.details["actual"] == 0

    def test_surrounding_whitespace_trimmed(self):
        check_content("  " + "客" * 20 + "  ", 20)


class TestLocationNote:
    """R-03：超出围栏必须填写说明。"""

    def test_out_of_fence_with_note_ok(self):
        check_location_note(LocationStatus.OUT_OF_FENCE, "客户临时改到分公司")

    def test_out_of_fence_without_note_raises(self):
        with pytest.raises(ValidationFailed) as exc:
            check_location_note(LocationStatus.OUT_OF_FENCE, None)
        assert exc.value.code == "R-03"

    def test_out_of_fence_blank_note_raises(self):
        with pytest.raises(ValidationFailed):
            check_location_note(LocationStatus.OUT_OF_FENCE, "   ")

    def test_normal_needs_no_note(self):
        check_location_note(LocationStatus.NORMAL, None)


class TestAttachmentLimits:
    """R-08：附件数量与体积上限。"""

    def test_image_within_limit(self):
        check_attachment_limits(
            existing_images=0, existing_voices=0, incoming_type=FileType.IMAGE,
            incoming_size=1024, max_images=9, max_voices=1, max_bytes=20 * 1024 * 1024,
        )

    def test_ninth_image_ok_tenth_rejected(self):
        """边界值：第 9 张放行，第 10 张拒绝。"""
        check_attachment_limits(
            existing_images=8, existing_voices=0, incoming_type=FileType.IMAGE,
            incoming_size=1024, max_images=9, max_voices=1, max_bytes=20 * 1024 * 1024,
        )
        with pytest.raises(ValidationFailed) as exc:
            check_attachment_limits(
                existing_images=9, existing_voices=0, incoming_type=FileType.IMAGE,
                incoming_size=1024, max_images=9, max_voices=1, max_bytes=20 * 1024 * 1024,
            )
        assert exc.value.code == "R-08"

    def test_oversized_file_rejected(self):
        with pytest.raises(ValidationFailed) as exc:
            check_attachment_limits(
                existing_images=0, existing_voices=0, incoming_type=FileType.IMAGE,
                incoming_size=20 * 1024 * 1024 + 1, max_images=9, max_voices=1,
                max_bytes=20 * 1024 * 1024,
            )
        assert exc.value.code == "R-08"

    def test_exactly_max_bytes_allowed(self):
        """边界值：恰好 20MB 放行。"""
        check_attachment_limits(
            existing_images=0, existing_voices=0, incoming_type=FileType.IMAGE,
            incoming_size=20 * 1024 * 1024, max_images=9, max_voices=1,
            max_bytes=20 * 1024 * 1024,
        )

    def test_second_voice_rejected(self):
        with pytest.raises(ValidationFailed):
            check_attachment_limits(
                existing_images=0, existing_voices=1, incoming_type=FileType.VOICE,
                incoming_size=1024, max_images=9, max_voices=1, max_bytes=20 * 1024 * 1024,
            )

    def test_documents_not_counted_against_image_limit(self):
        check_attachment_limits(
            existing_images=9, existing_voices=0, incoming_type=FileType.DOC,
            incoming_size=1024, max_images=9, max_voices=1, max_bytes=20 * 1024 * 1024,
        )


class TestOfflineDrift:
    """R-10：离线补传时间偏差。"""

    def test_within_threshold(self):
        abnormal, reason = evaluate_offline_drift(BASE, BASE + timedelta(minutes=10), 30)
        assert abnormal is False and reason is None

    def test_exactly_at_threshold_ok(self):
        abnormal, _ = evaluate_offline_drift(BASE, BASE + timedelta(minutes=30), 30)
        assert abnormal is False

    def test_beyond_threshold_abnormal(self):
        abnormal, reason = evaluate_offline_drift(BASE, BASE + timedelta(minutes=31), 30)
        assert abnormal is True and reason

    def test_negative_drift_also_abnormal(self):
        """设备时间比服务器慢同样要标记。"""
        abnormal, _ = evaluate_offline_drift(BASE, BASE - timedelta(hours=2), 30)
        assert abnormal is True


class TestMockedLocation:
    def test_normal(self):
        abnormal, reason = evaluate_mocked_location(False)
        assert abnormal is False and reason is None

    def test_mocked_is_abnormal(self):
        abnormal, reason = evaluate_mocked_location(True)
        assert abnormal is True and reason
