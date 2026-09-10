"""测试数据构造工具。

集中放这里，避免各测试文件各写一份造数逻辑 —— 那种重复最终一定会漂移，
然后同一份数据在不同测试里表达不同含义。
"""

from __future__ import annotations

import io

from PIL import ExifTags, Image
from PIL.TiffImagePlugin import IFDRational


def decimal_to_dms(value: float) -> tuple[IFDRational, ...]:
    """十进制度 → EXIF 的 (度, 分, 秒) 有理数三元组（取绝对值）。

    秒保留三位小数：若截断成整秒，0.28 秒的误差约合 8.6 米，
    会让「解析结果是否准确」的断言失去意义。真实相机也常带小数秒。
    """
    value = abs(value)
    degrees = int(value)
    minutes_float = (value - degrees) * 60
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60
    return (
        IFDRational(degrees, 1),
        IFDRational(minutes, 1),
        IFDRational(round(seconds * 1000), 1000),
    )


def build_gps_jpeg(
    *,
    latitude: float | None = 22.5248,
    longitude: float | None = 113.9432,
    captured_at: str | None = "2026:09:10 09:30:00",
    make: str | None = "TestCam",
    size: tuple[int, int] = (60, 40),
) -> bytes:
    """生成带（或不带）GPS EXIF 的 JPEG 字节流。

    Pillow 无法写入负的有理数，因此南半球/西经通过 ``Ref`` 字段表达，
    这与真实相机的行为一致。
    """
    image = Image.new("RGB", size, "white")
    exif = image.getexif()

    if make:
        exif[ExifTags.Base.Make] = make
    if captured_at:
        exif[ExifTags.Base.DateTimeOriginal] = captured_at

    if latitude is not None and longitude is not None:
        exif[34853] = {
            ExifTags.GPS.GPSLatitudeRef: "S" if latitude < 0 else "N",
            ExifTags.GPS.GPSLatitude: decimal_to_dms(latitude),
            ExifTags.GPS.GPSLongitudeRef: "W" if longitude < 0 else "E",
            ExifTags.GPS.GPSLongitude: decimal_to_dms(longitude),
        }

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def build_plain_jpeg(size: tuple[int, int] = (60, 40)) -> bytes:
    """生成无 EXIF 的 JPEG —— 相册转存、微信压缩后的真实形态。"""
    image = Image.new("RGB", size, "white")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()
