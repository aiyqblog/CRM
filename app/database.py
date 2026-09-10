"""数据库引擎与会话管理。

开发/测试用 SQLite，生产可切 PostgreSQL —— 只改 ``CRM_DATABASE_URL``，
ORM 层不感知差异。
"""

from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def _ensure_sqlite_dir(url: str) -> None:
    """SQLite 文件所在目录不存在时自动创建，否则连接会失败。"""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return
    path = url[len(prefix):]
    if path in ("", ":memory:") or path.startswith("file:"):
        return
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

connect_args: dict[str, object] = {}
if settings.is_sqlite:
    # FastAPI 的请求在独立线程中执行，SQLite 默认禁止跨线程复用连接。
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    future=True,
)

if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_conn, _record):  # noqa: ANN001
        """SQLite 默认不强制外键，需显式开启，否则约束形同虚设。"""
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表。测试与首次启动使用。"""
    from app import models  # noqa: F401  确保模型已注册到 metadata

    Base.metadata.create_all(bind=engine)
