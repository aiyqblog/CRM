"""pytest 公共夹具。

关键点：**必须在导入 app 之前设置环境变量**。``app.config.settings`` 是
lru_cache 的单例，各模块在导入时就会读取它；晚于导入再改就无效，
测试会写进开发库 —— 这是最容易被忽略又最难排查的一类测试污染。
"""

from __future__ import annotations

import os
import shutil
import tempfile

_TMP_ROOT = tempfile.mkdtemp(prefix="crm-test-")
os.environ["CRM_DATABASE_URL"] = f"sqlite:///{_TMP_ROOT}/test.db".replace("\\", "/")
os.environ["CRM_UPLOAD_DIR"] = os.path.join(_TMP_ROOT, "uploads")
os.environ["CRM_SECRET_KEY"] = "test-secret-key"
os.environ["CRM_DEBUG"] = "false"
# 关键：把口令散列轮数从 60 万降到 1000。
# 种子数据要建 9 个用户，60 万轮散列一次约 0.7 秒 —— 9 个用户就是 6 秒，
# 而这个开销发生在**每个**需要数据库的测试之前。不降下来，整个套件
# 会从十几秒膨胀到十几分钟。
os.environ["CRM_PBKDF2_ITERATIONS"] = "1000"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings as _settings  # noqa: E402
from app.database import Base, SessionLocal, init_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.seed import SEED_PASSWORD, seed, seed_demo_projects  # noqa: E402

# --- 自检 -------------------------------------------------------------------
# 上面这些环境变量一旦某个失效，测试**不会报错**，只会变慢或写错库，
# 排查成本极高（曾经因为丢失一行导致每个测试慢 45 倍）。
# 所以这里显式断言，让配置问题在收集阶段就炸出来。
assert _settings.pbkdf2_iterations <= 10_000, (
    f"测试口令散列轮数未生效（当前 {_settings.pbkdf2_iterations}），"
    "请检查 conftest.py 顶部的 CRM_PBKDF2_ITERATIONS 设置。"
)
assert "crm-test-" in _settings.database_url, (
    f"测试未指向临时数据库（当前 {_settings.database_url}），可能污染开发库！"
)

#: 所有测试统一使用的口令
PASSWORD = SEED_PASSWORD


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """整个测试会话结束后清理临时目录。"""
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


@pytest.fixture(scope="session")
def _schema():
    """整个测试会话只建一次表。

    实测 ``create_all`` 在本机要 ~2 秒（11 张表，Windows + SQLite DDL 开销）。
    若每个测试都重建，上百个测试光建表就要几分钟 —— 在 CI 上看起来就像流水线卡死。
    表结构建一次，测试之间只清数据。
    """
    init_db()
    yield


@pytest.fixture
def fresh_database(_schema):
    """每个需要数据库的测试获得一套干净数据。

    **刻意不加 autouse** —— 纯函数单元测试根本用不到数据库，
    若强制每个测试都灌种子，整个套件会被无谓地拖慢数倍。

    用「清数据」而非「drop/create」：语义同样是完全隔离，
    但省掉了 DDL，单测开销从 ~3s 降到几十毫秒。
    """
    with SessionLocal() as session:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()

        info = seed(session)
        seed_demo_projects(session)

    yield info


@pytest.fixture
def db(fresh_database):
    with SessionLocal() as session:
        yield session


@pytest.fixture
def app():
    return create_app(with_web=True)


@pytest.fixture
def client(app, fresh_database):
    """带登录态操作空间的 HTTP 客户端（不自动跟随重定向）。"""
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def login_as(app, fresh_database):
    """按用户名生成**独立**的已登录客户端。

    必须独立：一个 ``TestClient`` 只有一个 cookie jar。若让多个角色
    共用同一个 client 实例，后登录的会覆盖先登录的会话 —— 于是
    「销售 B 不能看销售 A 的数据」这类用例会退化成「销售 B 不能看销售 B 的数据」，
    依然通过或依然失败，但已经测不到任何东西了。这类失效极难察觉，
    因此这里每次调用都新建客户端。
    """

    def _login(username: str, password: str = PASSWORD) -> TestClient:
        instance = TestClient(app, follow_redirects=False)
        response = instance.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        assert response.status_code == 200, f"登录失败 {username}: {response.text}"
        return instance

    return _login


@pytest.fixture
def as_sales(login_as):
    """华南一组销售 —— 正常业务用例的主力账号。"""
    return login_as("sales_a")


@pytest.fixture
def as_sales_same_team(login_as):
    """同组另一名销售，用于验证组内隔离。"""
    return login_as("sales_a2")


@pytest.fixture
def as_sales_other_team(login_as):
    """华东二组销售 —— 越权测试的对照组。"""
    return login_as("sales_b")


@pytest.fixture
def as_manager(login_as):
    return login_as("manager_a")


@pytest.fixture
def as_director(login_as):
    return login_as("director")


@pytest.fixture
def as_admin(login_as):
    return login_as("admin")


@pytest.fixture
def as_marketing(login_as):
    return login_as("market")


@pytest.fixture
def as_tech(login_as):
    return login_as("tech_a")


@pytest.fixture
def customer_id(fresh_database):
    """南山客户，已登记坐标，围栏判定用例的主力对象。"""
    return fresh_database["customers"]["CUS-HZ-001"]


@pytest.fixture
def customer_id_no_coords(fresh_database):
    """未登记坐标的客户（苏州），用于 R-06。"""
    return fresh_database["customers"]["CUS-SZ-003"]


@pytest.fixture
def user_ids(fresh_database):
    return fresh_database["users"]


#: 深圳南山客户登记坐标
CUSTOMER_LNG = 113.9432
CUSTOMER_LAT = 22.5248

#: 围栏内（约 110 米）
NEARBY_LNG = 113.9445
NEARBY_LAT = 22.5252

#: 围栏外（约 3.5 公里）
FAR_LNG = 113.9800
FAR_LAT = 22.5400

LONG_CONTENT = "本次拜访与客户技术负责人确认了模组选型方向，客户倾向先做小批量验证后再量产。"
