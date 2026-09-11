"""后加列的补列与回填（Issue #5）。

为什么值得单独测：``Base.metadata.create_all`` **只创建缺失的表，不会给已存在的表加列**。
开发库、演示库、任何有数据的库都是「已存在」的，所以补列一旦失效，
表现是每次查询都报 ``no such column: sales_project.project_category`` —— 整站不可用，
而单元/接口测试全跑在新建的临时库上，一点异常都看不出来。

这里用一个「老结构」的库把这条路径钉死：老库 → 补列 → 回填 → 幂等。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.constants import DEFAULT_PROJECT_CATEGORY, ProjectCategory
from app.database import ensure_new_columns

pytestmark = pytest.mark.unit

#: Issue #5 之前的 sales_project 表结构（只保留判定所需的列）
LEGACY_SCHEMA = """
CREATE TABLE sales_project (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_no VARCHAR(32) NOT NULL,
    project_name VARCHAR(200) NOT NULL
)
"""

NEW_COLUMNS = {"project_category", "product_series", "product_model", "expected_dwin_date"}


def _legacy_engine(tmp_path) -> sa.Engine:
    """建一个只有老列、并带着一行存量数据的库。"""
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(sa.text(LEGACY_SCHEMA))
        conn.execute(
            sa.text(
                "INSERT INTO sales_project (project_no, project_name) "
                "VALUES ('PRJ-LEGACY-1', '存量项目')"
            )
        )
    return engine


def _columns(engine: sa.Engine) -> set[str]:
    return {col["name"] for col in sa.inspect(engine).get_columns("sales_project")}


def _category_of_legacy_row(engine: sa.Engine):
    with engine.begin() as conn:
        return conn.execute(
            sa.text("SELECT project_category FROM sales_project WHERE project_no='PRJ-LEGACY-1'")
        ).scalar()


def test_new_columns_are_added_to_existing_table(tmp_path):
    engine = _legacy_engine(tmp_path)
    assert not (NEW_COLUMNS & _columns(engine)), "前置条件不成立：老库不该有这些列"

    added = ensure_new_columns(engine)

    assert NEW_COLUMNS <= _columns(engine)
    assert set(added) == {f"sales_project.{name}" for name in NEW_COLUMNS}


def test_legacy_rows_get_default_category(tmp_path):
    """Issue #5 确认过：存量项目按「小型项目」归类，不留空。"""
    engine = _legacy_engine(tmp_path)

    ensure_new_columns(engine)

    assert _category_of_legacy_row(engine) == int(ProjectCategory.SMALL)
    assert int(DEFAULT_PROJECT_CATEGORY) == int(ProjectCategory.SMALL)


def test_topup_is_idempotent(tmp_path):
    engine = _legacy_engine(tmp_path)

    ensure_new_columns(engine)
    second = ensure_new_columns(engine)

    assert second == [], "重复执行不应再报新增列"


def test_backfill_does_not_overwrite_later_edits(tmp_path):
    """回填只在补列那一次发生 —— 否则每次启动都会把人工调整过的类别抹掉。"""
    engine = _legacy_engine(tmp_path)
    ensure_new_columns(engine)

    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE sales_project SET project_category = 1"))

    ensure_new_columns(engine)

    assert _category_of_legacy_row(engine) == int(ProjectCategory.LARGE)


def test_missing_table_is_skipped(tmp_path):
    """表还不存在时应安静跳过 —— 建表是 create_all 的职责，这里不越权。"""
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'empty.db').as_posix()}")

    assert ensure_new_columns(engine) == []


def test_fresh_schema_already_has_new_columns(db):
    """新建的库必须一次到位，不依赖补列兜底。"""
    engine = db.get_bind()

    assert NEW_COLUMNS <= _columns(engine)
