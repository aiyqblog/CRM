"""认证接口测试。"""

from __future__ import annotations

import pytest

from tests.conftest import PASSWORD

pytestmark = pytest.mark.api


def test_login_success(client):
    response = client.post(
        "/api/auth/login", json={"username": "sales_a", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["user"]["username"] == "sales_a"
    assert response.json()["user"]["role"] == "sales"


def test_login_sets_httponly_cookie(client):
    response = client.post(
        "/api/auth/login", json={"username": "sales_a", "password": PASSWORD}
    )
    cookie_header = response.headers.get("set-cookie", "")
    assert "crm_session" in cookie_header
    assert "HttpOnly" in cookie_header


def test_login_wrong_password(client):
    response = client.post(
        "/api/auth/login", json={"username": "sales_a", "password": "wrong"}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH-401"


def test_login_unknown_user_same_error(client):
    """不区分「用户不存在」与「密码错」，避免账号枚举。"""
    response = client.post(
        "/api/auth/login", json={"username": "nobody", "password": "whatever"}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH-401"


def test_me_after_login(as_sales):
    response = as_sales.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["user"]["display_name"] == "王销售"


def test_me_without_login(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH-401"


def test_protected_endpoint_without_login(client):
    response = client.get("/api/customers")
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH-401"


def test_tampered_cookie_rejected(client):
    client.cookies.set("crm_session", "not-a-valid-signature")
    response = client.get("/api/auth/me")
    assert response.status_code == 403


def test_logout_clears_session(as_sales):
    as_sales.post("/api/auth/logout")
    response = as_sales.get("/api/auth/me")
    assert response.status_code == 403


def test_password_hash_is_salted():
    """同一口令两次散列结果必须不同（盐生效）。"""
    from app.services.auth import hash_password, verify_password

    first = hash_password("same-password")
    second = hash_password("same-password")
    assert first != second
    assert verify_password("same-password", first)
    assert verify_password("same-password", second)


def test_verify_rejects_malformed_hash():
    from app.services.auth import verify_password

    assert verify_password("x", "garbage") is False
    assert verify_password("x", "pbkdf2_sha256$notanint$aa$bb") is False


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
