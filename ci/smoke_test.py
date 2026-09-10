"""部署后冒烟测试。

需求文档里说的冒烟：部署完成后，用几分钟判断这个构建「是不是基本可用」。
任何一条不过就立刻打回，不要浪费时间去跑全量回归。

只用标准库，不依赖 httpx —— 冒烟脚本要在最干净的环境里也能跑起来。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

DEFAULT_BASE_URL = "http://127.0.0.1:18080"
DEFAULT_CREDENTIALS = ("sales_a", "crm123456")


class SmokeFailure(Exception):
    pass


class Client:
    """带 cookie 的极简 HTTP 客户端。"""

    def __init__(self, base_url: str, timeout: int = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )

    def request(self, method: str, path: str, payload: dict | None = None):
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
                return response.status, body
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            raise SmokeFailure(f"无法访问 {url}：{exc}") from exc

    def get(self, path: str):
        return self.request("GET", path)

    def post(self, path: str, payload: dict | None = None):
        return self.request("POST", path, payload)


def check(name: str, condition: bool, detail: str = "") -> bool:
    mark = "\033[32m✓\033[0m" if condition else "\033[31m✗\033[0m"
    suffix = f"  {detail}" if detail else ""
    print(f"  {mark} {name}{suffix}")
    return condition


def run(base_url: str, username: str, password: str) -> bool:
    client = Client(base_url)
    results: list[bool] = []

    print(f"\n冒烟测试目标：{base_url}\n")

    # 1. 服务活着
    #
    # 这里必须解析 JSON，不能用字符串匹配：FastAPI 的 JSON 序列化输出是
    # {"status":"ok"} 而不是 {"status": "ok"}（冒号后没有空格），
    # 用带空格的字面量去匹配会得到「HTTP 200 但判定失败」这种自相矛盾的结果。
    status, body = client.get("/health")
    try:
        health_payload = json.loads(body)
        healthy = status == 200 and health_payload.get("status") == "ok"
    except json.JSONDecodeError:
        healthy = False
    results.append(check("服务健康检查 /health", healthy, f"HTTP {status}"))

    # 2. 登录
    status, body = client.post(
        "/api/auth/login", {"username": username, "password": password}
    )
    logged_in = status == 200
    results.append(check(f"登录 {username}", logged_in, f"HTTP {status}"))

    # 3. 关键读接口（三类核心数据）
    for name, path in [
        ("客户列表", "/api/customers"),
        ("拜访记录", "/api/visits"),
        ("销售项目", "/api/projects"),
        ("销售漏斗", "/api/projects/funnel"),
    ]:
        status, _ = client.get(path)
        results.append(check(f"读取{name}", status == 200, f"HTTP {status}"))

    # 4. 页面渲染（前端没崩）
    for name, path in [("登录页", "/login"), ("看板页", "/")]:
        status, body = client.get(path)
        ok = status == 200 and "<html" in body.lower()
        results.append(check(f"渲染{name}", ok, f"HTTP {status}"))

    # 5. 漏斗必须返回 8 个阶段（业务模型没被改坏）
    status, body = client.get("/api/projects/funnel")
    if status == 200:
        try:
            stages = json.loads(body)["stages"]
            ok = len(stages) == 8
            detail = f"{len(stages)} 个阶段"
        except (KeyError, json.JSONDecodeError):
            ok, detail = False, "响应结构异常"
    else:
        ok, detail = False, f"HTTP {status}"
    results.append(check("漏斗返回 8 个销售阶段", ok, detail))

    return all(results)


def main() -> int:
    parser = argparse.ArgumentParser(description="部署后冒烟测试")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--username", default=DEFAULT_CREDENTIALS[0])
    parser.add_argument("--password", default=DEFAULT_CREDENTIALS[1])
    args = parser.parse_args()

    try:
        passed = run(args.base_url, args.username, args.password)
    except SmokeFailure as exc:
        print(f"  \033[31m✗\033[0m {exc}")
        print("\n\033[31m冒烟测试失败：服务不可达\033[0m")
        return 1

    if passed:
        print("\n\033[32m冒烟测试通过 —— 构建基本可用\033[0m")
        return 0

    print("\n\033[31m冒烟测试失败 —— 立即打回，不要继续跑全量回归\033[0m")
    return 1


if __name__ == "__main__":
    sys.exit(main())
