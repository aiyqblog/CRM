"""FastAPI 应用装配。

统一错误契约（重要）：所有错误响应形状一致
``{"code": "...", "message": "...", "details": {...}}``，
``code`` 直接对应需求说明书的规则编号。这样接口测试可以断言 ``code == "R-20"``，
而不是去匹配中文文案（文案一改测试就挂）。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import auth, customers, projects, visits
from app.config import settings
from app.database import init_db
from app.services.errors import DomainError


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    os.makedirs(settings.upload_dir, exist_ok=True)
    yield


def create_app(*, with_web: bool = True) -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # --- 错误契约 ---------------------------------------------------------
    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "REQ-422",
                "message": "请求参数校验失败",
                "details": {"errors": exc.errors()},
            },
        )

    # --- 路由 -------------------------------------------------------------
    app.include_router(auth.router)
    app.include_router(customers.router)
    app.include_router(visits.router)
    app.include_router(projects.router)

    if with_web:
        from app.web import pages

        app.include_router(pages.router)

    # --- 静态资源 ---------------------------------------------------------
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    os.makedirs(static_dir, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # --- 健康检查（部署后冒烟测试的探针） ---------------------------------
    @app.get("/health", tags=["ops"])
    def health() -> dict:
        return {"status": "ok", "version": __version__, "app": settings.app_name}

    return app


app = create_app()
