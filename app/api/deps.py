"""FastAPI 依赖：会话解析与当前用户。

会话用 itsdangerous 签名 cookie，无服务端状态。好处是 E2E 测试里浏览器
自动携带 cookie，与真实用户体验一致（换 JWT 也一样，但签名 cookie 更少代码）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User
from app.services.errors import PermissionDenied

_serializer = URLSafeSerializer(settings.secret_key, salt="crm-session")


def sign_session(user_id: int) -> str:
    return _serializer.dumps({"uid": user_id})


def load_session(token: str) -> int | None:
    try:
        payload = _serializer.loads(token)
    except BadSignature:
        return None
    uid = payload.get("uid") if isinstance(payload, dict) else None
    return int(uid) if uid is not None else None


def get_optional_user(
    request: Request, db: Annotated[Session, Depends(get_db)]
) -> User | None:
    """未登录返回 None，供页面路由做跳转判断。"""
    token = request.cookies.get(settings.session_cookie)
    if not token:
        return None
    user_id = load_session(token)
    if user_id is None:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


def get_current_user(
    user: Annotated[User | None, Depends(get_optional_user)],
) -> User:
    """接口路由使用；未登录直接 403。"""
    if user is None:
        raise PermissionDenied("请先登录", code="AUTH-401")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[Session, Depends(get_db)]
