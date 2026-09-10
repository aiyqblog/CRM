"""EXIF 提取单元测试 —— 防作弊能力的地基。

如果这里出错，整个「服务端读取拍摄坐标」的设计就是纸面的。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.services.exif import extract_capture_metadata
from tests.helpers import build_gps_jpeg, build_plain_jpeg

pytestmark = pytest.mark.unit


class TestGpsExtraction:
    def test_north_east_coordinates(self):
        """深圳南山：北纬东经。"""
        meta = extract_capture_metadata(
            build_gps_jpeg(latitude=22.5248, longitude=113.9432)
        )
        assert meta.has_gps
        assert meta.latitude == pytest.approx(22.5248, abs=1e-5)
        assert meta.longitude == pytest.approx(113.9432, abs=1e-5)

    def test_south_west_coordinates_are_negative(self):
        """南纬西经必须转成负数，否则坐标会落在完全错误的位置。"""
        meta = extract_capture_metadata(
            build_gps_jpeg(latitude=-33.8688, longitude=-70.6693)
        )
        assert meta.latitude == pytest.approx(-33.8688, abs=1e-5)
        assert meta.longitude == pytest.approx(-70.6693, abs=1e-5)

    def test_south_latitude_north_longitude(self):
        meta = extract_capture_metadata(
            build_gps_jpeg(latitude=-6.2088, longitude=106.8456)
        )
        assert meta.latitude < 0
        assert meta.longitude > 0

    def test_zero_degrees(self):
        """赤道本初子午线附近：0 也是有效坐标，不能被当成缺失。"""
        meta = extract_capture_metadata(build_gps_jpeg(latitude=0.0, longitude=0.0))
        assert meta.has_gps
        assert meta.latitude == 0.0
        assert meta.longitude == 0.0


class TestDatetimeExtraction:
    def test_captured_at_parsed(self):
        meta = extract_capture_metadata(build_gps_jpeg(captured_at="2026:09:10 09:30:00"))
        assert meta.captured_at == datetime(2026, 9, 10, 9, 30, 0)

    def test_missing_datetime_gives_none(self):
        meta = extract_capture_metadata(build_gps_jpeg(captured_at=None))
        assert meta.captured_at is None

    def test_malformed_datetime_gives_none_not_error(self):
        meta = extract_capture_metadata(build_gps_jpeg(captured_at="not a date"))
        assert meta.captured_at is None


class TestDegradation:
    """降级路径必须健壮：真实场景里大量照片就是没有 EXIF 的。"""

    def test_plain_jpeg_has_no_metadata(self):
        meta = extract_capture_metadata(build_plain_jpeg())
        assert meta.captured_at is None
        assert meta.latitude is None
        assert meta.longitude is None
        assert not meta.has_gps

    def test_gps_present_but_no_datetime(self):
        meta = extract_capture_metadata(build_gps_jpeg(captured_at=None))
        assert meta.has_gps
        assert meta.captured_at is None

    def test_corrupt_bytes_do_not_raise(self):
        """损坏文件不能让整个上传接口 500。"""
        meta = extract_capture_metadata(b"this is definitely not an image")
        assert meta.latitude is None and meta.captured_at is None

    def test_empty_bytes_do_not_raise(self):
        meta = extract_capture_metadata(b"")
        assert meta.latitude is None

    def test_truncated_jpeg_do_not_raise(self):
        data = build_gps_jpeg()
        meta = extract_capture_metadata(data[: len(data) // 2])
        assert isinstance(meta.latitude, (float, type(None)))


class TestDeviceInfo:
    def test_make_extracted(self):
        meta = extract_capture_metadata(build_gps_jpeg(make="Xiaomi 14"))
        assert meta.device_make == "Xiaomi 14"

    def test_no_make(self):
        meta = extract_capture_metadata(build_gps_jpeg(make=None))
        assert meta.device_make is None
