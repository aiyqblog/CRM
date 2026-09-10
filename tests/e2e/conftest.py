"""E2E 专用夹具：起一个真实的服务进程 + 真实浏览器。

为什么不用 TestClient：E2E 的意义就在于验证「真的能跑起来」——
进程启动、静态资源、cookie、重定向、浏览器渲染，任何一环断了 TestClient 都发现不了。

数据隔离：E2E 用独立的库文件与上传目录，且服务跑在子进程里，
与进程内的单元/接口测试完全不共享状态。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

CRM_ROOT = Path(__file__).resolve().parents[2]
STARTUP_TIMEOUT = 60


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(url: str, timeout: int = STARTUP_TIMEOUT) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = exc
        time.sleep(0.3)

    raise RuntimeError(f"服务在 {timeout} 秒内未就绪: {url}（最后一次错误：{last_error}）")


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    """启动 uvicorn 子进程，返回基地址。"""
    data_dir = tmp_path_factory.mktemp("e2e-data")
    db_file = data_dir / "e2e.db"
    upload_dir = data_dir / "uploads"

    env = os.environ.copy()
    env.update(
        {
            "CRM_DATABASE_URL": f"sqlite:///{db_file.as_posix()}",
            "CRM_UPLOAD_DIR": upload_dir.as_posix(),
            "CRM_SECRET_KEY": "e2e-secret-key",
            "CRM_PBKDF2_ITERATIONS": "1000",
            "CRM_DEBUG": "false",
            "PYTHONIOENCODING": "utf-8",
        }
    )

    # 先建表并灌种子数据
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.database import init_db; from app.database import SessionLocal;"
            "from app.seed import seed, seed_demo_projects;"
            "init_db();"
            "db = SessionLocal(); seed(db); seed_demo_projects(db); db.close();"
            "print('seed ok')",
        ],
        cwd=CRM_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=CRM_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(f"{base_url}/health")
    except RuntimeError:
        process.terminate()
        output = process.stdout.read() if process.stdout else ""
        raise RuntimeError(f"E2E 服务启动失败，进程输出：\n{output}") from None

    yield base_url

    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture(scope="session")
def base_url(live_server):
    """覆盖 pytest-base-url 的默认值，让 page.goto('/xxx') 直接可用。"""
    return live_server


@pytest.fixture(scope="session")
def browser_type_launch_args():
    """使用系统已安装的 Chrome，避免下载 150MB 的 chromium。

    若环境变量指定了其他 channel（如 msedge / chromium），以环境变量为准。
    """
    channel = os.environ.get("CRM_E2E_BROWSER_CHANNEL", "chrome")
    args: dict = {"headless": True}
    if channel and channel != "chromium":
        args["channel"] = channel
    return args


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """统一设置视口与语言，避免不同机器上布局差异导致定位失败。"""
    return {
        **browser_context_args,
        "viewport": {"width": 1440, "height": 900},
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
    }


class CrmPage:
    """页面操作封装。

    把「怎么点、怎么等」集中在一处：选择器或等待策略一旦变化只需改这里，
    不用在几十个测试里逐个替换。
    """

    PASSWORD = "crm123456"

    def __init__(self, page, base_url: str) -> None:
        self.page = page
        self.base_url = base_url

    # --- 导航 -------------------------------------------------------------
    def goto(self, path: str = "/"):
        self.page.goto(f"{self.base_url}{path}")
        return self

    def submit(self, test_id: str, timeout: int = 15_000):
        """点击提交按钮，并等待重定向真正完成。

        **不要用 ``wait_for_load_state("networkidle")`` 等表单提交。**
        点击之前当前页面通常就已经是 networkidle，该调用会立即返回，
        后续断言便落在旧页面上 —— 这是 E2E 里最典型的假失败来源，
        而失败信息看起来像「业务没生效」，极易误导排查方向。

        这里用 ``expect_navigation``：它等待「由这次点击触发的一次导航」。
        比「等某个元素出现」更准 —— 若页面上恰好已经存在同样的元素
        （例如上一次操作留下的 flash），等元素会立即返回，又变成竞态。
        若表单因 HTML5 校验未通过而压根没提交，这里会超时，
        这正是我们想要的失败信号。
        """
        with self.page.expect_navigation(wait_until="load", timeout=timeout):
            self.page.get_by_test_id(test_id).click()
        return self

    def fill_and_submit(self, fields: dict[str, str], test_id: str, timeout: int = 15_000):
        """填写多个字段后提交。

        注意：本应用的每次提交都是一次完整往返，页面重载后**所有表单字段会被清空**。
        因此「先提交失败 → 再补填一个字段 → 再提交」这种写法必须把之前的字段一起重填，
        否则会得到一个看似是业务校验失败、实则是自己没填的问题。
        """
        for selector, value in fields.items():
            self.page.locator(selector).fill(value)
        return self.submit(test_id, timeout=timeout)

    # --- 登录 -------------------------------------------------------------
    def login(self, username: str, password: str | None = None):
        """登录。

        登录成功时重定向不携带 msg（不会出现 flash），
        因此这里等待的是「登录框消失或首页出现」这一对互斥信号，
        而不是加载状态。
        """
        self.goto("/login")
        self.page.get_by_test_id("login-username").fill(username)
        self.page.get_by_test_id("login-password").fill(password or self.PASSWORD)
        self.page.get_by_test_id("login-submit").click()
        self.page.locator(
            '[data-testid="nav-dashboard"], [data-testid="login-error"]'
        ).first.wait_for(state="visible", timeout=15_000)
        return self

    def logout(self):
        self.page.get_by_test_id("logout").click()
        self.page.get_by_test_id("login-form").wait_for(state="visible", timeout=15_000)
        return self

    # --- 断言辅助 ---------------------------------------------------------
    @property
    def flash(self) -> str:
        locator = self.page.get_by_test_id("flash")
        return locator.inner_text() if locator.count() else ""

    @property
    def current_user(self) -> str:
        return self.page.get_by_test_id("current-user").inner_text()

    def contains(self, text: str) -> bool:
        return text in self.page.content()

    @property
    def path(self) -> str:
        """当前 URL 的路径部分（不含查询串），便于断言。"""
        from urllib.parse import urlparse

        return urlparse(self.page.url).path


@pytest.fixture
def crm(page, base_url):
    """未登录的页面对象。"""
    return CrmPage(page, base_url)


# --- 用例间状态隔离 ---------------------------------------------------------
# E2E 服务是会话级的：启动一次约 3 秒，为 25 个用例各起一次要多花 75 秒，
# 不值得为此付出代价。但代价是数据会跨用例累积，而 R-04
# 「同一拜访人不允许存在重叠的未签退记录」会让后续用例莫名失败 ——
# 一个用例忘了签退，后面所有同账号的签到用例都会挂，且错误信息完全指向别处。
#
# 这里通过**公开 API** 归还状态，而不是直接写数据库：
# 既避免与运行中的服务抢 SQLite 文件锁，也不用在生产代码里开测试后门。

_E2E_USERS = ["sales_a", "sales_a2", "manager_a"]


def _close_dangling_visits(base_url: str, username: str) -> int:
    import json
    import urllib.request
    from http.cookiejar import CookieJar

    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar())
    )

    def send(method: str, path: str, payload: dict | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        request = urllib.request.Request(
            f"{base_url}{path}", data=data, headers=headers, method=method
        )
        with opener.open(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        send("POST", "/api/auth/login", {"username": username, "password": "crm123456"})
    except Exception:  # noqa: BLE001 - 清理失败不应让用例失败
        return 0

    closed = 0
    try:
        visits = send("GET", "/api/visits?limit=200")["items"]
    except Exception:  # noqa: BLE001
        return 0

    for visit in visits:
        if visit.get("status") != 2:  # 只处理"进行中"
            continue
        try:
            send(
                "POST",
                f"/api/visits/{visit['id']}/checkout",
                {"content": "E2E 用例间清理：自动签退遗留的进行中拜访记录。"},
            )
            closed += 1
        except Exception:  # noqa: BLE001
            pass

    return closed


@pytest.fixture(autouse=True)
def clean_dangling_visits(live_server):
    """每个 E2E 用例开始前，把遗留的「进行中」拜访签退掉。"""
    for username in _E2E_USERS:
        _close_dangling_visits(live_server, username)
    yield


# --- 已登录角色 -------------------------------------------------------------
# 每个 fixture 由 pytest 为每个用例单独实例化，因此同一个用例里同时请求
# 两个角色也是安全的（前提是同时请求 page —— 一个用例只有一个 page 实例，
# 需要多角色并发操作时用 browser.new_context() 另开上下文）。


@pytest.fixture
def as_sales_crm(crm):
    return crm.login("sales_a")


@pytest.fixture
def as_manager_crm(crm):
    return crm.login("manager_a")


@pytest.fixture
def as_marketing_crm(crm):
    return crm.login("market")


@pytest.fixture
def as_director_crm(crm):
    return crm.login("director")
