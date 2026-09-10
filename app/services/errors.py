"""领域异常。

``code`` 一律使用需求说明书中的规则编号（R-01、R-20、PERM-403 …），
这样接口返回的错误可以直接反查到文档条款，测试也能按编号断言。
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """业务规则违反。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        http_status: int = 400,
    ) -> None:
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}
        self.http_status = http_status
        super().__init__(f"[{code}] {message}")


class ValidationFailed(DomainError):
    def __init__(self, code: str, message: str, **kw: Any) -> None:
        kw.setdefault("http_status", 400)
        super().__init__(code, message, **kw)


class PermissionDenied(DomainError):
    def __init__(self, message: str = "无权访问该数据", code: str = "PERM-403") -> None:
        super().__init__(code, message, http_status=403)


class NotFound(DomainError):
    def __init__(self, message: str = "对象不存在", code: str = "NOT-FOUND") -> None:
        super().__init__(code, message, http_status=404)


class Conflict(DomainError):
    def __init__(self, code: str, message: str, **kw: Any) -> None:
        kw.setdefault("http_status", 409)
        super().__init__(code, message, **kw)


class AuthFailed(DomainError):
    def __init__(self, message: str = "用户名或密码错误") -> None:
        super().__init__("AUTH-401", message, http_status=401)
