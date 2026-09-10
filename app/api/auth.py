"""认证接口。"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.deps import CurrentUser, DbSession, sign_session
from app.api.schemas import LoginIn
from app.api.serializers import user_out
from app.config import settings
from app.services import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(payload: LoginIn, response: Response, db: DbSession) -> dict:
    user = auth.authenticate(db, payload.username, payload.password)
    response.set_cookie(
        key=settings.session_cookie,
        value=sign_session(user.id),
        httponly=True,
        samesite="lax",
        max_age=settings.session_max_age,
        path="/",
    )
    return {"user": user_out(user)}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(settings.session_cookie, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: CurrentUser) -> dict:
    return {"user": user_out(user)}
