# CRM 拜访与项目管理

参照销售易 CRM 自建，覆盖「拜访记录」与「销售项目」两个模块。

需求规格见仓库根目录的 **《CRM-拜访记录与销售项目模块-开发需求说明书》**。
本 README 只讲工程侧怎么跑起来。

---

## 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| Web 框架 | FastAPI | 单文件即可挂载 API 与页面路由，OpenAPI 自带 |
| ORM | SQLAlchemy 2.0 | 类型化 Mapped 声明；SQLite → PostgreSQL 只改连接串 |
| 数据库 | SQLite（开发/测试） | 零部署成本；生产切 PostgreSQL 无需改模型 |
| 模板 | Jinja2 服务端渲染 | 少一层前端构建，把复杂度留给流水线本身 |
| 测试 | pytest + Playwright | 单测/接口/E2E 同一套 runner |
| 部署 | Docker + Compose | 环境一致性 |

> 前端是服务端渲染 + 少量原生 JS。要换 React 不影响后端与流水线设计，
> 只替换 `app/templates` 与 `app/web` 即可。

## 目录结构

```
crm/
├── app/
│   ├── constants.py        领域常量：角色 / 状态 / 8 阶段 / 门禁产出物 / 业务阈值
│   ├── config.py           配置（业务阈值均可由环境变量覆盖）
│   ├── database.py         引擎与会话
│   ├── models/             ORM 模型（11 张表）
│   ├── services/           业务逻辑 —— 校验规则全部在这一层
│   │   ├── visit.py        拜访规则 R-01 ~ R-13
│   │   ├── project.py      项目规则 R-20 ~ R-30
│   │   ├── gate.py         阶段门禁
│   │   ├── permission.py   行级数据权限
│   │   ├── exif.py         图片 EXIF 提取（防作弊的核心）
│   │   └── storage.py      附件存储、压缩、时效签名
│   ├── api/                REST 接口
│   ├── web/                页面路由（服务端渲染）
│   ├── templates/          Jinja2 模板
│   └── seed.py             种子数据（测试与演示共用）
├── tests/
│   ├── unit/               纯函数与规则判定
│   ├── api/                接口 + 页面 + 越权 + 渲染契约
│   └── e2e/                Playwright 真浏览器
├── ci/
│   ├── run_pipeline.py     本地流水线执行器
│   └── smoke_test.py       部署后冒烟
├── .gitlab-ci.yml          CI 定义（与本地流水线阶段一致）
├── Dockerfile
└── docker-compose.yml
```

---

## 快速开始

### 本地跑起来

```bash
pip install -r requirements-dev.txt

# 灌入演示数据并启动
CRM_SEED_DEMO=true python -m app.bootstrap
uvicorn app.main:app --reload --port 8000
```

浏览器打开 http://127.0.0.1:8000 ，用演示账号登录（口令统一 `crm123456`）：

| 账号 | 角色 | 用途 |
|---|---|---|
| `sales_a` | 销售 | 主力业务账号 |
| `sales_a2` | 销售 | 同组同事，验证组内隔离 |
| `sales_b` | 销售 | 另一团队，越权测试对照 |
| `manager_a` | 销售主管 | 门禁覆盖、团队数据 |
| `director` | 销售总监 | 全量可见 |
| `market` | 市场 | 只读角色 |
| `admin` | 管理员 | 全部权限 |

### 跑测试

```bash
pytest tests/unit tests/api    # 快，约 30 秒
pytest tests/e2e -m e2e        # 真浏览器，约 2~3 分钟
```

E2E 默认用系统已装的 Chrome（`CRM_E2E_BROWSER_CHANNEL=chrome`），
不下载 chromium。装了 Playwright 浏览器的话设成 `chromium` 也行。

### 跑完整流水线

```bash
python ci/run_pipeline.py              # 静态检查 → 单测 → 接口 → E2E → 构建 → 部署 → 冒烟
python ci/run_pipeline.py --no-docker  # 跳过容器部分
python ci/run_pipeline.py --only lint unit
python ci/run_pipeline.py --from api   # 从某个阶段开始
python ci/run_pipeline.py --list       # 看阶段列表
```

失败时：完整日志在 `ci-logs/<阶段>.log`，结构化报告在 `ci-report.json`。

### 容器部署

```bash
docker compose up -d --build
python ci/smoke_test.py --base-url http://127.0.0.1:18080
```

---

## 流水线

阶段划分在 `.gitlab-ci.yml` 与 `ci/run_pipeline.py` 中**保持严格一致**。
本地绿、CI 红这类不一致比没有本地流水线更糟 —— 会让人不再信任本地结果。

```
lint  →  unit  →  api  →  e2e  →  build  →  deploy  →  smoke
静态检查  单测    接口    端到端   构建镜像   测试环境   冒烟
```

**生产发布不在这条流水线里。** 数据迁移不可逆、缺少回滚窗口、需要变更审批留痕，
必须人工放行。流水线只自动部署到测试环境。

### 内网部署前必须确认三件事

这三条是网络可达性问题，不是配置技巧问题，不解决流水线第一步就过不去：

1. **镜像仓库代理** —— `.gitlab-ci.yml` 里的 `python:3.13-slim`、
   `mcr.microsoft.com/playwright/python` 需换成内网 Harbor 地址
2. **pip 私有源** —— `PIP_INDEX_URL` 指向内网 Nexus
3. **Playwright 浏览器** —— `PLAYWRIGHT_DOWNLOAD_HOST` 指向内网镜像，
   否则容器里下载浏览器会超时

---

## 约定

- **业务阈值集中在 `app/constants.py` 与 `app/config.py`**，不要在业务代码里写魔法数字
- **校验规则一律编号**（R-01、R-20…），错误响应的 `code` 就是规则编号 ——
  测试断言 `code` 而不是中文文案，文案会改，编号是契约
- **所有按 id 访问资源的接口都要过权限判定**，直接 `db.get` 是最典型的越权漏洞来源
- **模板里渲染枚举必须用裸值**，详见 `app/web/pages.py::_plain_keyed` 的说明 ——
  曾经因此出过一次只有浏览器才能发现的线上故障
- **不要用 `wait_for_load_state("networkidle")` 等表单提交**，
  见 `tests/e2e/conftest.py::CrmPage.submit` 的说明

## 已知边界

- 附件存本地文件系统，生产应换对象存储（只需改 `services/storage.py::save_bytes`）
- 编号生成用「查当日最大值 +1」，并发下有竞争窗口，生产应改数据库序列
- 报备冲突进裁决队列，但裁决界面尚未实现
- 未接入单点登录；会话是签名 cookie
