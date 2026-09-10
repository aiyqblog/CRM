"""GitHub Issue → 自动开发 → 测试 → PR 的工作流骨架。

设计分工（这是本文件存在的核心理由）：
    本脚本负责**所有确定性动作** —— 拉 Issue、打标签、建分支、提交、跑流水线、
    推送、开 PR、留评论。这些步骤不该让 LLM 去判断，脚本执行才可复现、可审计。
    「怎么改代码」这一件事才交给 Agent。

透明性：所有状态都放在 git 分支与 GitHub 标签上，不落本地数据库。
    好处是跨多次自动化运行、跨机器、跨人都能接续；人也能一眼看出哪个 Issue
    正在被处理、哪个卡住了。

用法：
    python ci/github_flow.py doctor
    python ci/github_flow.py list-issues --limit 5
    python ci/github_flow.py claim 12
    python ci/github_flow.py verify
    python ci/github_flow.py commit 12 -m "feat: ..."
    python ci/github_flow.py push 12
    python ci/github_flow.py pr 12
    python ci/github_flow.py fail 12 -m "原因"
    python ci/github_flow.py done 12

凭据：
    只从环境变量 ``GITHUB_TOKEN`` 读取，**绝不写入仓库、不写进技能文档**。
    推送时通过 ``git -c http.extraheader`` 传递，因此 token 也不会落进
    ``.git/config``。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"


def _load_dotenv() -> None:
    """从项目根的 ``.env`` 载入配置（若存在）。

    存在的意义是让凭据有个**不进入聊天记录、也不进入仓库**的落脚点：
    ``.env`` 已在 .gitignore 中，token 写在那里比贴在对话里安全得多。
    已存在的环境变量优先，不会被文件覆盖。
    """
    env_file = ROOT / ".env"
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

# --- Agent 工作流标签 -------------------------------------------------------
LABEL_QUEUED = "agent:queued"        # 待处理
LABEL_WORKING = "agent:working"      # 处理中（防重复领取）
LABEL_REVIEW = "agent:review"        # 已开 PR，待人工审查
LABEL_BLOCKED = "agent:blocked"      # 自动化失败，需人工介入
#: 带这些标签的 Issue 一律跳过 —— 它们需要人的判断，不是代码问题
LABEL_SKIP = {"agent:blocked", "needs-design", "question", "wontfix"}

#: 分支前缀。**刻意不含斜杠。**
#:
#: 原先是 ``agent/issue-``，但本机环境下 git 无法在 ``.git/refs/heads/`` 下
#: 创建子目录 —— ``git update-ref refs/heads/agent/issue-2`` 会返回 0 却什么也不写，
#: 还会把刚建好的目录一起删掉，分支因此永远处于"未出生"状态，后续 commit 变成孤儿提交。
#: 而单层引用（``refs/heads/agent-issue-2``）完全正常。
#:
#: 这不只是本地开发的问题：自动化用的是同一套环境，嵌套分支名会让整条链路静默失效。
#: 用扁平命名换取可靠性，代价仅是分支名里少一个斜杠。
BRANCH_PREFIX = "agent-issue-"
BASE_BRANCH = "main"

#: 单条 git 命令的最长等待时间。联网操作（fetch/push）在此之内没结果就中止，
#: 避免自动化在无人值守时无声挂起。
_GIT_TIMEOUT = 180


class FlowError(Exception):
    """工作流层面的错误，带上人话解释。"""


# ==========================================================================
# GitHub API
# ==========================================================================
def token() -> str:
    value = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not value:
        raise FlowError(
            "未设置 GITHUB_TOKEN 环境变量。\n"
            "  请到 GitHub → Settings → Developer settings → Personal access tokens 生成，\n"
            "  细粒度令牌需要的权限：Contents(RW)、Issues(RW)、Pull requests(RW)、Metadata(R)。"
        )
    return value.strip()


def repository() -> str:
    value = os.environ.get("GITHUB_REPOSITORY")
    if value:
        return value.strip()

    # 回退：从 origin 远程地址推断
    try:
        url = _git("remote", "get-url", "origin").stdout.strip()
    except FlowError:
        raise FlowError(
            "无法确定仓库。请设置 GITHUB_REPOSITORY=owner/repo，或先配置 origin 远程。"
        ) from None

    match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)", url)
    if not match:
        raise FlowError(f"无法从远程地址解析出 owner/repo：{url}")
    return f"{match.group('owner')}/{match.group('repo')}"


def api(
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    accept: str = "application/vnd.github+json",
):
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None

    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token()}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "crm-agent-flow",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body.strip() else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise FlowError(f"GitHub API {method} {path} 返回 {exc.code}：{detail}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise FlowError(f"无法访问 GitHub API：{exc}") from None


# ==========================================================================
# git
# ==========================================================================
def _git(*args: str, check: bool = True, use_token: bool = False) -> subprocess.CompletedProcess:
    # 一律禁用凭据助手。
    #
    # 本机全局配置了 ``credential.helper = helper-selector``，它在拿不到凭据时会
    # **卡住等待**——实测挂满 60 秒才超时，而且 stderr 是空的，自动化表现为
    # 「进程被外部杀掉、什么都没输出」，排查时毫无线索。
    # 凭据统一由 ``http.extraheader`` 显式传入，不需要也不该让助手介入。
    command = ["git", "-c", "credential.helper="]
    if use_token:
        # 通过 extraheader 传凭据，token 不会写进 .git/config
        credential = base64.b64encode(
            f"x-access-token:{token()}".encode("utf-8")
        ).decode("ascii")
        command += ["-c", f"http.extraheader=AUTHORIZATION: basic {credential}"]

    command += list(args)

    # 关掉一切交互式提示，配合上面的 helper 禁用，确保任何失败都在秒级返回。
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "Never",
        "GIT_ASKPASS": "",
    }
    try:
        result = subprocess.run(  # noqa: S603
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise FlowError(
            f"git {' '.join(args)} 超过 {_GIT_TIMEOUT}s 无响应，已中止。\n"
            "  通常是网络/代理不通。本机 git 走 HTTP_PROXY，代理异常时会返回 502。"
        ) from None

    if check and result.returncode != 0:
        raise FlowError(
            f"git {' '.join(args)} 失败（rc={result.returncode}）：\n{result.stderr.strip()}"
        )
    return result


def current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def working_tree_clean() -> bool:
    return not _git("status", "--porcelain").stdout.strip()


# ==========================================================================
# 分支与 slug
# ==========================================================================
def slugify(title: str, limit: int = 40) -> str:
    """把标题转成可放进分支名的片段。

    中文标题会被完全过滤掉 —— 这是刻意的：分支名里的中文在 CI、
    终端、URL 里都可能出问题，而 PR 标题本身已经带够了信息。
    """
    ascii_only = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()
    return ascii_only[:limit].strip("-")


def branch_for(issue: dict) -> str:
    slug = slugify(issue.get("title", ""))
    return f"{BRANCH_PREFIX}{issue['number']}-{slug}" if slug else f"{BRANCH_PREFIX}{issue['number']}"


def issue_number_from_branch(branch: str | None = None) -> int | None:
    name = branch or current_branch()
    match = re.match(rf"{re.escape(BRANCH_PREFIX)}(\d+)", name)
    return int(match.group(1)) if match else None


def get_issue(number: int) -> dict:
    owner_repo = repository()
    return api("GET", f"/repos/{owner_repo}/issues/{number}")


def label_names(issue: dict) -> set[str]:
    return {label["name"] for label in issue.get("labels", [])}


def set_labels(number: int, add: list[str], remove: list[str]) -> None:
    owner_repo = repository()
    for name in add:
        try:
            api("POST", f"/repos/{owner_repo}/issues/{number}/labels", {"labels": [name]})
        except FlowError as exc:
            # 标签不存在时自动创建，避免第一次运行就卡住
            if "404" in str(exc) or "422" in str(exc):
                api(
                    "POST",
                    f"/repos/{owner_repo}/labels",
                    {"name": name, "color": "1D76DB", "description": "Agent 工作流标签"},
                )
                api("POST", f"/repos/{owner_repo}/issues/{number}/labels", {"labels": [name]})
            else:
                raise

    for name in remove:
        try:
            api("DELETE", f"/repos/{owner_repo}/issues/{number}/labels/{urllib.parse.quote(name)}")
        except FlowError:
            pass  # 标签本来就不在，忽略


def comment(number: int, body: str) -> str:
    owner_repo = repository()
    result = api("POST", f"/repos/{owner_repo}/issues/{number}/comments", {"body": body})
    return result["html_url"]


# ==========================================================================
# 子命令
# ==========================================================================
def cmd_doctor(_args) -> int:
    print("环境自检\n")

    ok = True

    # token
    try:
        tok = token()
        print(f"  ✓ GITHUB_TOKEN 已设置（{len(tok)} 字符）")
    except FlowError as exc:
        print(f"  ✗ {exc}")
        return 1

    # 仓库
    try:
        owner_repo = repository()
        print(f"  ✓ 仓库：{owner_repo}")
    except FlowError as exc:
        print(f"  ✗ {exc}")
        ok = False
        owner_repo = None

    # API 可达 + 权限
    if owner_repo:
        try:
            repo = api("GET", f"/repos/{owner_repo}")
            print(f"  ✓ API 可达（{'私有' if repo.get('private') else '公开'}仓库，默认分支 {repo.get('default_branch')}）")
        except FlowError as exc:
            print(f"  ✗ {exc}")
            ok = False

    # 写权限：必须真的探一次，不能看 ``repo["permissions"]``
    #
    # ``permissions`` 反映的是「**登录用户**对该仓库的角色」，不是「令牌被授予的权限」。
    # 对自己的仓库它恒为 push=True，于是引擎不可写时自检依然报绿，直到 push 阶段才炸出
    # ``remote: Write access to repository not granted``——排查方向全被带偏。
    #
    # 用一个只创建悬空对象、不改动任何 ref 的写入探测替代它。
    if owner_repo:
        probe_write = True
        try:
            api(
                "POST",
                f"/repos/{owner_repo}/git/blobs",
                {"content": "crm-flow-permission-probe", "encoding": "utf-8"},
            )
            print("  ✓ 令牌有写权限（Contents: Read and write）")
            probe_write = False
        except FlowError as exc:
            message = str(exc)
            if "409" in message or "410" in message or "empty" in message.lower():
                # 仓库还没有任何提交，GitHub 无法创建 blob，无从探测 —— 不是权限问题
                print("  ! 仓库为空，写权限将在首次 push 时验证")
                probe_write = False
            elif "404" in message or "403" in message:
                print("  ✗ 令牌缺少写权限：需要 Contents = Read and write")
                print(f"    当前仓库不在令牌的授权范围内时同样会返回 404（{owner_repo}）")
                ok = False
                probe_write = False
        if probe_write:
            print("  ! 写权限探测未得出结论，继续执行")

    # git 状态
    try:
        branch = current_branch()
        print(f"  ✓ 当前分支：{branch}")
        if branch == BASE_BRANCH:
            print(f"    （工作从 {BASE_BRANCH} 建新分支开始，不会直接改 {BASE_BRANCH}）")
    except FlowError as exc:
        print(f"  ✗ {exc}")
        ok = False

    dirty = not working_tree_clean()
    print(f"  {'!' if dirty else '✓'} 工作区{'有未提交改动' if dirty else '干净'}")

    print()
    print("自检通过" if ok else "自检未通过，请先解决上面标 ✗ 的问题")
    return 0 if ok else 1


def _eligible(issue: dict) -> bool:
    if "pull_request" in issue:
        return False  # /issues 接口会混入 PR
    labels = label_names(issue)
    if labels & LABEL_SKIP:
        return False
    if LABEL_WORKING in labels:
        return False
    assignees = issue.get("assignees") or []
    # 已指派给人的不碰 —— 那说明有人在做
    if any(not a.get("login", "").endswith("[bot]") for a in assignees):
        return False
    return True


def _priority_key(issue: dict) -> tuple:
    labels = label_names(issue)
    # 优先级：显式 agent:queued > priority 标签 > 创建时间早的优先
    explicit = 0 if LABEL_QUEUED in labels else 1
    high = 0 if {"priority:high", "P0", "bug"} & labels else 1
    return (explicit, high, issue["created_at"])


def cmd_list_issues(args) -> int:
    owner_repo = repository()
    issues = api(
        "GET",
        f"/repos/{owner_repo}/issues?state=open&per_page=100&sort=created&direction=asc",
    )

    candidates = sorted((i for i in issues if _eligible(i)), key=_priority_key)
    if args.limit:
        candidates = candidates[: args.limit]

    if not candidates:
        print("没有可领取的 Issue")
        print(f"  （跳过条件：带 {'/'.join(sorted(LABEL_SKIP))} 或 {LABEL_WORKING} 标签、已被指派给人、是 PR）")
        return 0

    print(f"发现 {len(candidates)} 个可处理 Issue：\n")
    for issue in candidates:
        labels = ",".join(sorted(label_names(issue))) or "-"
        print(f"  #{issue['number']:<4} {issue['title']}")
        print(f"        标签：{labels}")
        print(f"        链接：{issue['html_url']}")
    print()
    print(json.dumps([i["number"] for i in candidates]))
    return 0


def cmd_claim(args) -> int:
    number = args.number
    issue = get_issue(number)

    if "pull_request" in issue:
        raise FlowError(f"#{number} 是 PR，不是 Issue")

    labels = label_names(issue)
    if labels & LABEL_SKIP:
        raise FlowError(f"#{number} 带有跳过标签：{sorted(labels & LABEL_SKIP)}")
    if LABEL_WORKING in labels:
        branch = branch_for(issue)
        print(f"#{number} 已在处理中，复用它（分支 {branch}）")
        _git("checkout", branch, check=False)
        print(branch)
        return 0

    if not working_tree_clean():
        raise FlowError(
            "工作区有未提交改动，拒绝开始新任务。\n"
            "  请先 commit 或 stash —— 否则后续 commit 会把无关改动混进来。"
        )

    branch = branch_for(issue)

    # fetch 只为刷新旧 ref，失败不该阻断任务 —— 本地已有 origin/<base> 可直接开分支。
    # 本机 git 走 HTTP_PROXY，代理偶发 502，因一次抖动就让整个 Issue 处理失败太脆。
    fetched = _git("fetch", "origin", BASE_BRANCH, use_token=True, check=False)
    if fetched.returncode != 0:
        detail = fetched.stderr.strip().splitlines()
        print(f"  ! 拉取 {BASE_BRANCH} 失败，改用本地已有的 origin/{BASE_BRANCH} 开分支")
        if detail:
            print(f"    {detail[-1][:120]}")

    _git("checkout", "-B", branch, f"origin/{BASE_BRANCH}")

    set_labels(number, add=[LABEL_WORKING], remove=[LABEL_QUEUED])
    comment(
        number,
        f"🤖 已开始自动处理。\n\n分支：`{branch}`\n"
        f"流程：编码 → 静态检查 → 单测 → 接口测试 → E2E → 提交 → PR\n\n"
        f"完成后会自动开 PR 等待人工审查，**不会直接合并**。",
    )

    print(f"已领取 #{number}")
    print(f"  分支：{branch}")
    print(f"  基于：{BASE_BRANCH}")
    print(branch)
    return 0


def cmd_verify(args) -> int:
    """跑本地流水线（不含容器阶段）。"""
    command = [
        sys.executable,
        str(ROOT / "ci" / "run_pipeline.py"),
        "--no-docker",
    ]
    if args.only:
        command += ["--only", *args.only]

    result = subprocess.run(command, cwd=ROOT)  # noqa: S603
    return result.returncode


def cmd_commit(args) -> int:
    number = args.number
    if working_tree_clean():
        raise FlowError("没有需要提交的改动")

    branch = current_branch()
    if branch == BASE_BRANCH:
        raise FlowError(
            f"拒绝在 {BASE_BRANCH} 上提交。请先用 `claim` 建分支。"
        )

    issue = get_issue(number)
    message = args.message.strip()
    if f"#{number}" not in message:
        message = f"{message}\n\nRefs #{number}"

    _git("add", "-A")
    _git("commit", "-m", message)

    print(f"已提交到 {branch}")
    print(_git("log", "-1", "--pretty=format:%h %s").stdout.strip())
    print(f"  Issue: #{number} {issue['title']}")
    return 0


def cmd_push(args) -> int:
    number = args.number
    branch = current_branch()
    expected = branch_for(get_issue(number))
    if branch != expected:
        raise FlowError(
            f"当前分支 {branch} 与 Issue #{number} 的预期分支 {expected} 不一致。\n"
            "  拒绝推送，避免把改动推到错误的分支上。"
        )
    if branch == BASE_BRANCH:
        raise FlowError(f"拒绝推送到 {BASE_BRANCH}")

    # use_token=True 让凭据通过 -c http.extraheader 传入，
    # 不会写进 .git/config，也不会出现在 `git remote -v` 里
    _git("push", "-u", "origin", branch, use_token=True)

    print(f"已推送 {branch}")
    return 0


def _test_summary() -> str:
    """从流水线报告里取测试结果，写进 PR 描述。"""
    report_path = ROOT / "ci-report.json"
    if not report_path.exists():
        return "（未找到 ci-report.json，测试结果未记录）"

    report = json.loads(report_path.read_text(encoding="utf-8"))
    lines = []
    for stage in report.get("stages", []):
        mark = "✅" if stage["passed"] else "❌"
        lines.append(f"| {mark} | `{stage['stage']}` | {stage['title']} | {stage['duration_s']}s |")
    lines.append(f"\n总耗时：{report.get('total_duration_s')}s")
    return "\n".join(lines)


def cmd_pr(args) -> int:
    number = args.number
    issue = get_issue(number)
    branch = current_branch()
    owner_repo = repository()

    existing = api(
        "GET",
        f"/repos/{owner_repo}/pulls?head={repository().split('/')[0]}:{branch}&state=all",
    )
    if existing:
        print(f"该分支已有 PR：{existing[0]['html_url']}")
        return 0

    commits = _git("log", f"origin/{BASE_BRANCH}..HEAD", "--pretty=format:%h %s").stdout.strip()
    files = _git("diff", "--stat", f"origin/{BASE_BRANCH}...HEAD").stdout.strip()

    body = f"""## 关联

Closes #{number}

## 改动

由自动化流程根据 Issue 描述实现，**待人工审查**。

```
{files or '（无文件变更）'}
```

提交记录：
```
{commits or '（无提交）'}
```

## 流水线结果

| 结果 | 阶段 | 说明 | 耗时 |
|---|---|---|---|
{_test_summary()}

## 审查要点

- [ ] 改动是否真的解决了 Issue 描述的问题
- [ ] 是否引入了 Issue 未要求的额外改动
- [ ] 涉及权限（`app/services/permission.py`）或门禁（`app/services/gate.py`）时，重点核对逻辑
- [ ] 新增行为是否有对应测试
- [ ] 模板字段名/枚举渲染是否遵守契约（`test_form_contract.py` 会检查）

---
*本 PR 由自动化流程创建，不会自动合并。*
"""

    result = api(
        "POST",
        f"/repos/{owner_repo}/pulls",
        {
            "title": f"[#{number}] {issue['title']}",
            "head": branch,
            "base": BASE_BRANCH,
            "body": body,
            "draft": args.draft,
        },
    )

    set_labels(number, add=[LABEL_REVIEW], remove=[LABEL_WORKING])
    comment(number, f"🤖 已提交 PR：{result['html_url']}\n\n等待人工审查与合并。")

    print(f"已创建 PR：{result['html_url']}")
    return 0


def cmd_fail(args) -> int:
    number = args.number
    reason = args.message.strip()

    set_labels(number, add=[LABEL_BLOCKED], remove=[LABEL_WORKING])
    comment(
        number,
        f"🤖 自动化处理未能完成，已标记为 `{LABEL_BLOCKED}`，**需要人工介入**。\n\n"
        f"原因：\n\n```\n{reason}\n```\n\n"
        f"移除 `{LABEL_BLOCKED}` 标签并重新打上 `{LABEL_QUEUED}` 可以让自动化重试。",
    )

    print(f"#{number} 已标记为阻塞")
    return 0


def cmd_done(args) -> int:
    number = args.number
    set_labels(number, add=[LABEL_REVIEW], remove=[LABEL_WORKING])
    comment(number, "🤖 开发与测试已完成，PR 已提交，等待人工审查。")
    print(f"#{number} 已收尾")
    return 0


def current_login() -> str:
    return api("GET", "/user")["login"]


def cmd_init_repo(args) -> int:
    """创建 GitHub 仓库（默认私有）、配置 origin、推送 main。

    幂等：仓库已存在时跳过创建，直接配置远程并推送 ——
    重跑不会报错，也不会覆盖已有内容。
    """
    login = current_login()
    owner_repo = args.repository or f"{login}/{args.name}"

    # --- 1. 创建仓库（若不存在）------------------------------------------
    try:
        repo = api("GET", f"/repos/{owner_repo}")
        print(f"仓库已存在：{repo['html_url']}")
    except FlowError as exc:
        if "404" not in str(exc):
            raise
        try:
            repo = api(
                "POST",
                "/user/repos",
                {
                    "name": args.name,
                    "private": not args.public,
                    "auto_init": False,
                    # 不勾选 auto_init：仓库里已有完整历史，初始化 README 会造成分叉
                    "description": "CRM 拜访与销售项目管理系统（自建，参照销售易设计）",
                },
            )
        except FlowError as create_exc:
            if "403" in str(create_exc) or "401" in str(create_exc):
                raise FlowError(
                    "当前令牌无权创建仓库。\n"
                    "  细粒度令牌默认不能建仓。请二选一：\n"
                    "    a) 到 GitHub 网页手动建一个空仓库（不要勾选 README/gitignore/license），\n"
                    "       然后重跑本命令并加 --repository <owner>/<repo>\n"
                    "    b) 换一个具备建仓权限的令牌"
                ) from None
            raise
        print(f"已创建{'私有' if repo['private'] else '公开'}仓库：{repo['html_url']}")

    if not repo.get("private", True) and not args.public:
        print("  提示：仓库当前是公开的。公司代码建议改为私有（Settings → Danger Zone → Change visibility）")

    # --- 2. 配置 origin ---------------------------------------------------
    remote_url = f"https://github.com/{owner_repo}.git"
    existing = _git("remote", check=False).stdout.split()
    if "origin" in existing:
        _git("remote", "set-url", "origin", remote_url)
    else:
        _git("remote", "add", "origin", remote_url)
    print(f"已配置 origin → {remote_url}")
    print("  （远程地址里不含 token，凭据在推送时临时注入）")

    # --- 3. 推送 main -----------------------------------------------------
    branch = current_branch()
    if branch != BASE_BRANCH:
        raise FlowError(
            f"当前分支是 {branch}，不是 {BASE_BRANCH}。请先切回 main 再推送初始代码。"
        )

    try:
        _git("push", "-u", "origin", BASE_BRANCH, use_token=True)
    except FlowError as exc:
        message = str(exc)
        if "Write access to repository not granted" in message:
            raise FlowError(
                "推送被拒：令牌通过了认证，但**没有写权限**。\n"
                "  这与「令牌过期」是两回事 —— 认证成功、授权失败。\n"
                "  检查该细粒度令牌的 Permissions（不是 Repository access）：\n"
                "    Contents        Read and write   ← 缺这个就会报本条错误\n"
                "    Issues          Read and write\n"
                "    Pull requests   Read and write\n"
                "    Workflows       Read and write   ← 推 .github/workflows/ 需要\n"
                "    Metadata        Read-only（强制附带）\n"
                "  另外确认本仓库已加入令牌的 Repository access 列表。"
            ) from None
        if "without `workflow` scope" in message or "workflow" in message.lower():
            raise FlowError(
                "推送被拒：令牌缺少 Workflows 权限。\n"
                "  仓库里有 .github/workflows/ 文件，GitHub 要求令牌具备\n"
                "  Workflows = Read and write 才允许推送工作流文件。\n"
                "  补上该权限后重试，不要用绕过方式。"
            ) from None
        raise
    print(f"已推送 {BASE_BRANCH}")

    # --- 4. 收尾提示 ------------------------------------------------------
    print()
    print("接下来建议在 GitHub 上做两件事（都需要手动，API 改不了或不该由脚本改）：")
    print(f"  1. 给 {BASE_BRANCH} 加分支保护：Settings → Branches → 要求 PR 才能合并")
    print("     —— 这是自动化流水线的最后一道闸门，务必开启")
    print("  2. 仓库里创建 labels：agent:queued / agent:working / agent:review / agent:blocked")
    print("     （首次 claim 时会自动创建，也可以现在手动建）")
    print()
    print(f"仓库地址：{repo['html_url']}")
    return 0


def cmd_current(args) -> int:
    branch = current_branch()
    number = issue_number_from_branch(branch)
    print(json.dumps({"branch": branch, "issue": number, "clean": working_tree_clean()}))
    return 0


# ==========================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="GitHub Issue 自动化工作流")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="环境自检").set_defaults(func=cmd_doctor)

    p = sub.add_parser("init-repo", help="创建 GitHub 仓库、配置 origin 并推送 main")
    p.add_argument("--name", default="crm-visit-project", help="仓库名")
    p.add_argument("--repository", help="owner/repo；留空则用当前登录账号")
    p.add_argument("--public", action="store_true", help="创建为公开仓库（默认私有）")
    p.set_defaults(func=cmd_init_repo)

    p = sub.add_parser("list-issues", help="列出可处理的 Issue")
    p.add_argument("--limit", type=int, default=0, help="最多返回几个（0 表示不限）")
    p.set_defaults(func=cmd_list_issues)

    p = sub.add_parser("claim", help="领取 Issue：打标签并新建分支")
    p.add_argument("number", type=int)
    p.set_defaults(func=cmd_claim)

    p = sub.add_parser("verify", help="运行本地流水线")
    p.add_argument("--only", nargs="*", help="只跑指定阶段")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("commit", help="提交改动")
    p.add_argument("number", type=int)
    p.add_argument("-m", "--message", required=True)
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("push", help="推送当前分支")
    p.add_argument("number", type=int)
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("pr", help="创建 Pull Request")
    p.add_argument("number", type=int)
    p.add_argument("--draft", action="store_true", help="创建为草稿 PR")
    p.set_defaults(func=cmd_pr)

    p = sub.add_parser("fail", help="标记为阻塞并留评论")
    p.add_argument("number", type=int)
    p.add_argument("-m", "--message", required=True)
    p.set_defaults(func=cmd_fail)

    p = sub.add_parser("done", help="收尾：转为待审查")
    p.add_argument("number", type=int)
    p.set_defaults(func=cmd_done)

    sub.add_parser("current", help="输出当前分支与对应 Issue").set_defaults(func=cmd_current)

    args = parser.parse_args()

    try:
        return args.func(args)
    except FlowError as exc:
        print(f"\n\033[31m错误：{exc}\033[0m", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
