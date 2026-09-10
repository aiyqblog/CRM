"""本地 CI 流水线执行器。

设计要点：
  1. **阶段划分与 .gitlab-ci.yml 严格一致** —— 否则本地绿了、CI 红了，
     这种不一致比没有本地流水线更糟。
  2. **失败即停，且输出足够定位问题** —— 每个阶段保存完整日志到
     ``ci-logs/<stage>.log``，异常时打印末尾若干行。
  3. **结构化报告** —— 产出 ``ci-report.json``，让后续可以自动读取
     失败阶段与关键错误行，而不是靠人肉翻日志。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "ci-logs"
REPORT_PATH = ROOT / "ci-report.json"

PYTHON = sys.executable


@dataclass
class Stage:
    name: str
    title: str
    command: list[str]
    cwd: Path = field(default_factory=lambda: ROOT)
    needs_docker: bool = False
    #: 允许失败仍继续（用于「修复后再跑」的场景）
    allow_failure: bool = False
    env: dict[str, str] = field(default_factory=dict)

    @property
    def log_path(self) -> Path:
        return LOG_DIR / f"{self.name}.log"


def _stage_env(stage: Stage) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(ROOT))
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.update(stage.env)
    return env


def docker_available() -> bool:
    """检测 Docker 引擎是否可达。

    两个坑都踩过，都记在这里：

    1. 必须区分「客户端装了」与「引擎在跑」：``docker --version`` 在引擎没启动时
       也会成功，于是流水线跑到 build 阶段才抛出一句
       ``open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file``——
       这句报错既不提 Docker Desktop，也不说该怎么办。

    2. **不能用 ``docker info --format``**：引擎不可达时它把错误写到 stderr，
       但退出码依然是 **0**（模板格式化了一个空结果）。这样写出来的健康检查
       永远返回「可用」，等于没检查。``docker ps`` 在同样情况下正确返回 1。
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["docker", "ps", "--quiet"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False

    return result.returncode == 0


def build_stages(*, include_docker: bool) -> list[Stage]:
    stages: list[Stage] = [
        Stage(
            name="lint",
            title="静态检查（语法 + Ruff）",
            command=[PYTHON, "-m", "ruff", "check", "app", "tests", "ci"],
        ),
        Stage(
            name="unit",
            title="单元测试",
            command=[
                PYTHON, "-m", "pytest", "tests/unit",
                "-q", "--no-header", "-p", "no:cacheprovider",
            ],
        ),
        Stage(
            name="api",
            title="接口测试",
            command=[
                PYTHON, "-m", "pytest", "tests/api",
                "-q", "--no-header", "-p", "no:cacheprovider",
            ],
        ),
        Stage(
            name="e2e",
            title="端到端测试（Playwright）",
            command=[
                PYTHON, "-m", "pytest", "tests/e2e",
                "-q", "--no-header", "-p", "no:cacheprovider",
                "--tracing=retain-on-failure",
            ],
        ),
    ]

    if include_docker:
        stages += [
            Stage(
                name="build",
                title="构建镜像",
                command=["docker", "build", "-t", "crm:ci", "."],
                needs_docker=True,
            ),
            Stage(
                name="deploy",
                title="部署到本地测试环境",
                command=["docker", "compose", "up", "-d", "--force-recreate"],
                needs_docker=True,
            ),
            Stage(
                name="smoke",
                title="部署后冒烟测试",
                command=[PYTHON, "ci/smoke_test.py", "--base-url", "http://127.0.0.1:18080"],
                needs_docker=True,
            ),
        ]

    return stages


def _select_stages(all_stages: list[Stage], args: argparse.Namespace) -> list[Stage]:
    selected = all_stages

    if args.only:
        wanted = set(args.only)
        selected = [s for s in selected if s.name in wanted]
        missing = wanted - {s.name for s in selected}
        if missing:
            raise SystemExit(f"未知阶段：{', '.join(sorted(missing))}")

    if args.start:
        names = [s.name for s in all_stages]
        if args.start not in names:
            raise SystemExit(f"未知阶段：{args.start}")
        selected = [s for s in selected if names.index(s.name) >= names.index(args.start)]

    if args.skip:
        skipped = set(args.skip)
        selected = [s for s in selected if s.name not in skipped]

    return selected


def run_stage(stage: Stage) -> tuple[bool, float]:
    LOG_DIR.mkdir(exist_ok=True)
    started = time.time()

    with stage.log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        process = subprocess.run(  # noqa: S603
            stage.command,
            cwd=stage.cwd,
            env=_stage_env(stage),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        log_file.write(process.stdout or "")

    duration = time.time() - started
    passed = process.returncode == 0

    if passed:
        tail = _summarize_pass(process.stdout or "")
        print(f"  \033[32mPASS\033[0m  {duration:6.1f}s  {tail}")
    else:
        print(f"  \033[31mFAIL\033[0m  {duration:6.1f}s  （rc={process.returncode}）")
        for line in _extract_failure_lines(process.stdout or ""):
            print(f"        {line}")

    return passed, duration


def _summarize_pass(output: str) -> str:
    """从输出里提取一行有用的摘要，例如 '299 passed in 29.97s'。"""
    for line in reversed(output.strip().splitlines()):
        stripped = line.strip()
        if any(word in stripped for word in ("passed", "Successfully", "Built", "OK")):
            return stripped.strip("= ") if len(stripped) < 120 else stripped[:117] + "..."
    return "ok"


def _extract_failure_lines(output: str, limit: int = 8) -> list[str]:
    """抓出最有诊断价值的几行：失败用例名与断言错误。"""
    interesting: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(("FAILED ", "ERROR ")):
            interesting.append(stripped)
        elif stripped.startswith("E ") and len(interesting) < limit:
            interesting.append(stripped)

    if not interesting:
        interesting = output.strip().splitlines()[-limit:]

    return interesting[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description="本地 CI 流水线")
    parser.add_argument("--only", nargs="*", help="只跑指定阶段")
    parser.add_argument("--from", dest="start", help="从指定阶段开始跑")
    parser.add_argument("--skip", nargs="*", help="跳过指定阶段")
    parser.add_argument("--no-docker", action="store_true", help="跳过构建/部署/冒烟")
    parser.add_argument("--list", action="store_true", help="列出所有阶段")
    parser.add_argument("--keep-going", action="store_true", help="失败后继续跑后续阶段")
    args = parser.parse_args()

    include_docker = not args.no_docker

    # 引擎不可达时给出可操作的提示，而不是等到 build 阶段抛
    # "open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file"——
    # 那句报错既不提 Docker Desktop，也不说该怎么做。
    skipped_docker = False
    if include_docker and not docker_available():
        print(
            "\033[33m[跳过容器阶段]\033[0m Docker 引擎不可达。\n"
            "  客户端已安装不代表引擎在跑 —— 请启动 Docker Desktop 后重试，\n"
            "  或加 --no-docker 只跑代码流水线（lint / unit / api / e2e）。\n"
            "  容器阶段的配置可先用 docker compose config 校验语法。\n"
        )
        include_docker = False
        skipped_docker = True

    all_stages = build_stages(include_docker=include_docker)

    if args.list:
        print("流水线阶段：")
        for index, stage in enumerate(all_stages, 1):
            marker = " [docker]" if stage.needs_docker else ""
            print(f"  {index}. {stage.name:8s} {stage.title}{marker}")
        return 0

    selected = _select_stages(all_stages, args)
    if not selected:
        print("没有需要执行的阶段")
        return 0

    print(f"\n\033[1mCRM 流水线\033[0m  共 {len(selected)} 个阶段"
          f"  Python {sys.version.split()[0]}\n")

    results: list[dict] = []
    overall_start = time.time()
    failed_stage: str | None = None

    for stage in selected:
        print(f"\033[1m[{stage.name}]\033[0m {stage.title}")
        passed, duration = run_stage(stage)
        results.append(
            {
                "stage": stage.name,
                "title": stage.title,
                "passed": passed,
                "duration_s": round(duration, 2),
                "log": str(stage.log_path.relative_to(ROOT)),
            }
        )

        if not passed:
            failed_stage = stage.name
            if not (args.keep_going or stage.allow_failure):
                break

    total = time.time() - overall_start
    report = {
        "python": sys.version,
        "total_duration_s": round(total, 2),
        "failed_stage": failed_stage,
        "docker_stages_skipped": skipped_docker,
        "stages": results,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "─" * 58)
    for item in results:
        mark = "\033[32m✓\033[0m" if item["passed"] else "\033[31m✗\033[0m"
        print(f"  {mark} {item['stage']:8s} {item['duration_s']:6.1f}s  {item['title']}")
    if skipped_docker:
        print("  \033[33m–\033[0m build / deploy / smoke        已跳过（Docker 引擎不可达）")
    print("─" * 58)

    if failed_stage:
        print(f"\n\033[31m流水线失败于 [{failed_stage}]\033[0m")
        print(f"  完整日志：{LOG_DIR / f'{failed_stage}.log'}")
        print(f"  报告：{REPORT_PATH}")
        return 1

    print(f"\n\033[32m全部 {len(results)} 个阶段通过\033[0m  总耗时 {total:.1f}s")
    if skipped_docker:
        print(
            "\033[33m注意：容器阶段未执行，本次结果不代表部署链路可用。\033[0m"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
