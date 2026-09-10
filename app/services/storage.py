"""附件存储与图片压缩。

本地实现写文件系统；生产替换为对象存储（OSS/S3）只需改 ``save_bytes``。
需求文档 6 节要求附件 URL 带时效签名，本地实现用 HMAC 生成带过期的下载令牌。
"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import time
import uuid

from PIL import Image

from app.config import settings
from app.services.errors import ValidationFailed

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | {".mp3", ".m4a", ".wav", ".amr", ".mp4", ".pdf"}


def _storage_root() -> str:
    root = os.path.abspath(settings.upload_dir)
    os.makedirs(root, exist_ok=True)
    return root


def guess_file_type(filename: str) -> int:
    """按扩展名推断附件类型（FileType 枚举值）。"""
    ext = os.path.splitext(filename)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return 1
    if ext in {".mp3", ".m4a", ".wav", ".amr"}:
        return 2
    if ext == ".mp4":
        return 3
    return 4


def compress_image(data: bytes, target_bytes: int, *, max_side: int = 1920) -> bytes:
    """压缩图片到不超过 ``target_bytes``。

    逐步降低 quality，仍不达标则继续缩小边长。原图由调用方异步另存，
    这里只负责产出用于快速加载的压缩版本（R-09）。
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB") if image.mode not in ("RGB", "L") else image
            image.thumbnail((max_side, max_side))

            for quality in (85, 75, 65, 55, 45, 35):
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=quality, optimize=True)
                if buffer.tell() <= target_bytes:
                    return buffer.getvalue()

            # 依然超标：继续缩边
            while image.width > 320:
                image.thumbnail((image.width // 2, image.height // 2))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=60, optimize=True)
                if buffer.tell() <= target_bytes:
                    return buffer.getvalue()

            return buffer.getvalue()
    except Exception:
        # 非图片或损坏文件：原样返回，由上层按体积校验兜底
        return data


def save_bytes(data: bytes, *, filename: str, subdir: str = "") -> tuple[str, int]:
    """写入存储，返回 ``(相对 URL, 字节数)``。"""
    ext = os.path.splitext(filename)[1].lower()
    if ext and ext not in ALLOWED_EXTENSIONS:
        raise ValidationFailed("R-08", f"不支持的文件类型：{ext}")

    root = _storage_root()
    directory = os.path.join(root, subdir) if subdir else root
    os.makedirs(directory, exist_ok=True)

    stored_name = f"{uuid.uuid4().hex}{ext or '.bin'}"
    path = os.path.join(directory, stored_name)

    with open(path, "wb") as handle:
        handle.write(data)

    relative = f"/uploads/{subdir}/{stored_name}" if subdir else f"/uploads/{stored_name}"
    return relative.replace("//", "/"), len(data)


def resolve_local_path(file_url: str) -> str:
    """把 ``/uploads/...`` 形式的 URL 还原为本地路径。"""
    stripped = file_url.replace("/uploads/", "", 1).lstrip("/")
    return os.path.join(_storage_root(), *stripped.split("/"))


def build_watermark(
    *,
    captured_at,
    latitude: float | None,
    longitude: float | None,
    user_name: str,
    customer_name: str,
) -> str:
    """生成水印文本（需求文档 3.5 V-04）。"""
    parts: list[str] = []
    if captured_at:
        parts.append(captured_at.strftime("%Y-%m-%d %H:%M:%S"))
    if latitude is not None and longitude is not None:
        parts.append(f"{latitude:.5f},{longitude:.5f}")
    parts.append(user_name)
    parts.append(customer_name)
    return " | ".join(parts)


# --------------------------------------------------------------------------
# 时效签名（需求文档 6 节：附件 URL 带时效签名，有效期 15 分钟）
# --------------------------------------------------------------------------
_SIGN_TTL_SECONDS = 15 * 60


def download_route(file_url: str) -> str:
    """把存储路径映射到下载路由。

    存储路径是 ``/uploads/...``（文件系统视角），
    对外下载走 ``/api/files/...``（带鉴权与签名校验）。
    早期版本直接返回存储路径，结果签名链接指向一个不存在的路由 ——
    签名合法却 404，这种 bug 只有真的去点链接才会发现。
    """
    prefix = "/uploads/"
    if file_url.startswith(prefix):
        return "/api/files/" + file_url[len(prefix):]
    return file_url


def sign_download(file_url: str, ttl: int = _SIGN_TTL_SECONDS) -> str:
    expires = int(time.time()) + ttl
    payload = f"{file_url}:{expires}"
    signature = hmac.new(
        settings.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    return f"{download_route(file_url)}?e={expires}&s={signature}"


def verify_download(file_url: str, expires: int, signature: str) -> bool:
    if expires < int(time.time()):
        return False
    payload = f"{file_url}:{expires}"
    expected = hmac.new(
        settings.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    return hmac.compare_digest(expected, signature)
