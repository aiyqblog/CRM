"""图片 EXIF 提取 —— 防作弊的关键实现。

需求文档 3.3 表 3 规定：附件的拍摄时间与坐标必须由**服务端**从图片 EXIF
读取，不接受前端提交。这里就是那个「服务端」。

如果允许前端自报坐标，伪造只需改一个 JSON 字段；从 EXIF 读则必须真的
在拍摄时带了 GPS 的相机拍过渡——伪造门槛完全不同。
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime

from PIL import ExifTags, Image

_GPS_IFD = 34853
_EXIF_IFD = 34665


@dataclass(frozen=True)
class CaptureMetadata:
    """从图片中提取到的拍摄信息。缺失的字段为 None。"""

    captured_at: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    device_make: str | None = None
    device_model: str | None = None

    @property
    def has_gps(self) -> bool:
        return self.latitude is not None and self.longitude is not None


def _to_float(value) -> float:
    """EXIF 的数值可能是 IFDRational、tuple 或 int。"""
    if isinstance(value, tuple) and len(value) == 2:
        return value[0] / value[1]
    return float(value)


def _gps_to_decimal(coord, ref) -> float | None:
    """把 EXIF 的 (度, 分, 秒) 转成十进制度。"""
    try:
        degrees, minutes, seconds = (_to_float(part) for part in coord)
    except (TypeError, ValueError, ZeroDivisionError):
        return None

    decimal = degrees + minutes / 60.0 + seconds / 3600.0
    if isinstance(ref, bytes):
        ref = ref.decode("ascii", "ignore")
    if str(ref).upper() in ("S", "W"):
        decimal = -decimal
    return decimal


def extract_capture_metadata(data: bytes, max_pixels: int = 40_000_000) -> CaptureMetadata:
    """从图片字节流提取拍摄时间与坐标。

    解析失败一律返回全 None 的元数据，不抛异常 —— 图片没有 EXIF 是常态
    （相册转存、微信压缩都会丢），把它当成错误会让大量正常拜访录不进来。
    缺失的信息由 ``location_status`` 与异常标记去表达。
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            # 防御解压炸弹：超大图先降到安全尺寸再读 EXIF
            if image.width * image.height > max_pixels:
                image.thumbnail((4096, 4096))

            exif = image.getexif()
            if not exif:
                return CaptureMetadata()

            captured_at = None
            raw_datetime = exif.get(ExifTags.Base.DateTimeOriginal) or exif.get(
                ExifTags.Base.DateTime
            )
            if raw_datetime:
                try:
                    captured_at = datetime.strptime(
                        str(raw_datetime).strip(), "%Y:%m:%d %H:%M:%S"
                    )
                except ValueError:
                    captured_at = None

            latitude = longitude = None
            gps_ifd = exif.get_ifd(_GPS_IFD)

            if gps_ifd:
                lat_raw = gps_ifd.get(ExifTags.GPS.GPSLatitude)
                lat_ref = gps_ifd.get(ExifTags.GPS.GPSLatitudeRef)
                lng_raw = gps_ifd.get(ExifTags.GPS.GPSLongitude)
                lng_ref = gps_ifd.get(ExifTags.GPS.GPSLongitudeRef)

                if lat_raw is not None and lat_ref is not None:
                    latitude = _gps_to_decimal(lat_raw, lat_ref)
                if lng_raw is not None and lng_ref is not None:
                    longitude = _gps_to_decimal(lng_raw, lng_ref)

            make = exif.get(ExifTags.Base.Make)
            model = exif.get(ExifTags.Base.Model)

            return CaptureMetadata(
                captured_at=captured_at,
                latitude=latitude,
                longitude=longitude,
                device_make=str(make)[:64] if make else None,
                device_model=str(model)[:64] if model else None,
            )
    except Exception:
        # 任何解析异常都不应阻断上传
        return CaptureMetadata()
