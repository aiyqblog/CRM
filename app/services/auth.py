"""认证：口令散列与会话签发。

使用标准库 pbkdf2_hmac，避免引入 bcrypt 原生依赖（内网镜像里常有缺 wheel
的问题）。参数按 OWASP 建议取 600k 轮。
"""

from __future__ import annotations

import hashlib
import hmac
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User
from app.services.errors import AuthFailed

_ALGORITHM = "pbkdf2_sha256"
_SALT_BYTES = 16


def hash_password(password: str, *, iterations: int | None = None) -> str:
    """返回 ``pbkdf2_sha256$轮数$盐$散列`` 格式的字符串。

    轮数写入散列串本身，因此后续调高轮数不会让存量口令失效。
    """
    rounds = iterations or settings.pbkdf2_iterations
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"{_ALGORITHM}${rounds}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验口令。格式非法时返回 False 而不是抛异常。"""
    try:
        algorithm, iterations_str, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algorithm != _ALGORITHM:
        return False
    try:
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    # 定长比较，避免时间侧信道
    return hmac.compare_digest(actual, expected)


def authenticate(db: Session, username: str, password: str) -> User:
    """按用户名口令认证。失败一律抛同一个错误，不区分「用户不存在」与「密码错」。"""
    user = db.execute(
        select(User).where(User.username == username, User.is_active.is_(True))
    ).scalar_one_or_none()

    if user is None or not verify_password(password, user.password_hash):
        raise AuthFailed()

    return user


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    display_name: str,
    role: str,
    team_id: int | None = None,
    manager_id: int | None = None,
) -> User:
    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
        role=role,
        team_id=team_id,
        manager_id=manager_id,
    )
    db.add(user)
    db.flush()
    return user
