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
    ensure_new_columns()


#: 后加列的补列清单 (表名, 列名, DDL 类型)。
#: 为什么需要它：``create_all`` 只创建缺失的**表**，**不会**给已存在的表加列 ——
#: 老库从此在每次 SELECT 时报 ``no such column``。本项目没有引入 Alembic，
#: 这几行就是最小可用迁移。新增列一律可空，存量行由下面的回填写默认值。
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("sales_project", "project_category", "INTEGER"),
    ("sales_project", "product_series", "VARCHAR(64)"),
    ("sales_project", "product_model", "VARCHAR(64)"),
    ("sales_project", "expected_dwin_date", "DATE"),
)


def ensure_new_columns(bind=None) -> list[str]:  # noqa: ANN001
    """给已存在的表补上后加的列（幂等），返回本次真正新增的 ``表.列``。

    补列成功时顺带把存量行的「项目类别」回填为默认值 —— Issue #5 确认过：
    历史项目按小型项目归类，不显示为空。回填只在补列那一次执行，
    之后再启动不会被反复覆盖（免得抹掉人工调整过的值）。
    """
    from sqlalchemy import inspect, text

    from app.constants import DEFAULT_PROJECT_CATEGORY

    target = bind if bind is not None else engine
    inspector = inspect(target)
    tables = set(inspector.get_table_names())
    known: dict[str, set[str]] = {}
    added: list[str] = []

    with target.begin() as conn:
        for table, column, ddl_type in _ADDED_COLUMNS:
            if table not in tables:
                continue  # 表还不存在，create_all 会按新结构直接建好
            if table not in known:
                known[table] = {col["name"] for col in inspector.get_columns(table)}
            if column in known[table]:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
            known[table].add(column)
            added.append(f"{table}.{column}")

        if "sales_project.project_category" in added:
            conn.execute(
                text(
                    "UPDATE sales_project SET project_category = :value "
                    "WHERE project_category IS NULL"
                ),
                {"value": int(DEFAULT_PROJECT_CATEGORY)},
            )

    return added
