# 代码审查规则

> 本文件是审查本仓库改动时的**最高优先级检查清单**，供人工审查者与 AI 审查 agent 使用。
> 技术栈、目录与运行方式见 `README.md`；本文件只回答一个问题：**什么必须拦下来，什么只是建议。**

适用项目：`crm` —— FastAPI + SQLAlchemy 2.0 + Jinja2 服务端渲染 + pytest / Playwright。

---

## 0. 审查者须知

### 0.1 这份清单为什么存在

本项目发生过**两次线上级故障，且两次都不报错、测试全绿**：

| # | 故障 | 表现 | 为什么测试抓不到 |
|---|---|---|---|
| 1 | 模板字段名与后端 Form 参数名不一致 | 后端永远收到空串 → 抛出「XX 必填」→ 排查方向被引向"用户没填"而不是"后端没收到" | 接口测试直接构造请求体，绕过了模板 |
| 2 | 模板渲染枚举用了枚举成员而非裸值 | 页面渲染出 `VisitType.ROUTINE`，提交后 422 | 接口测试直接传整数，只有真人用浏览器才暴露 |

共同点：**测试全绿、日志无异常、功能"就是不好使"**。
下面「阻断项」里的每一条都不是风格偏好，而是这类静默故障的入口。

另外两处静默风险值得单独记住：

- **`permission.py` 改错不会报错，只会静默泄露数据。**
- **`app/models/*` 改列不会报错，已存在的库只是缺列。**

### 0.2 输出约定

- 严重度三档：`🔴 阻断`（应明确写"不应合并"）、`🟡 建议`、`🟣 既有问题`（非本次引入）。
- **每条结论必须给出 `文件:行` + 具体触发路径**（什么输入 → 什么错误结果）。
  给不出触发路径的判断，降级为 `🟡` 并在正文写明"未能确认"。
- 只审 **本次 diff 引入**的问题。发现既有问题标 `🟣`，不要据此否定整个 PR。
- 不要审"可以更抽象 / 更优雅"。本项目刻意保持分层直白，见 §3。

---

## 1. 阻断项

### B1 · 模板字段名必须与后端 Form 参数名逐字一致

**查什么**：`app/templates/*.html` 里的 `name="..."` ↔ `app/web/pages.py` 中对应路由函数的 `Form(...)` 参数名。

**为什么**：FastAPI 对缺失的 Form 字段**只取默认值、不报错**。名字写错（把 `close_reason` 写成 `reason`）的结果是字段永远是空串，然后抛出一个误导性的必填错误——排查方向被引到"用户没填"，而真因是"后端没收到"。

**判定**：逐个表单字段对照，**任一不一致即 🔴**。

**验证命令**：
```bash
pytest tests/api/test_form_contract.py
```

### B2 · 模板渲染枚举必须用裸值

```jinja
{# ❌ 渲染出 "VisitType.ROUTINE" #}
<option value="{{ key }}">
```

`class X(int, Enum)` 这类**混入枚举**的 `str()` 返回 `"VisitType.ROUTINE"`，只有 `IntEnum` 才返回数值。
`app/web/pages.py::_plain_keyed`（第 59 行）负责统一转成裸值。

**查什么**：本次改动若新增或修改了**传入模板的枚举字典**，是否也经 `_plain_keyed` 包了一层。
**未包一层即 🔴**（新增枚举字典时最容易漏）。

**验证命令**：
```bash
pytest tests/api/test_render_contract.py   # 断言 option value 是裸值且与 constants.py 一致
```

### B3 · 按 id 访问资源的接口必须过权限判定

**查什么**：
- 新增/修改的按 id 取数接口，是否调用了 `app/services/permission.py` 的判定函数，
  或经 `resolve_scope` 限定了数据范围。
  判定函数：`decide_read` / `decide_visit_edit` / `decide_visit_delete` /
  `decide_export` / `decide_project_close` / `decide_gate_override`
- 跨用户 / 跨团队的 id 是否可能被直接透传

**判定**：出现裸 `db.get(Model, id)` 且无权限判定 → **🔴**。

**验证命令**：`pytest tests/api/test_permissions.py`
**额外要求**：改动 `permission.py` **必须补越权测试用例**（`tests/api/test_permissions.py`）。

### B4 · 改了模型列必须同时写迁移

**为什么**：`create_all` **不会给已存在的表加列**。老库不受建表语句影响，改列后不提迁移，老库启动即缺列。

**查什么**：`app/models/*` 中新增 / 改名 / 改类型的列，是否在 `app/database.py` 的迁移路径（`ensure_new_columns()`）中同步；迁移是否**幂等**（先 inspector 判断再 `ALTER TABLE`）。

**判定**：模型加了列但迁移缺失 → **🔴**。

**验证命令**：
```bash
pytest tests/unit/test_schema_migration.py   # 覆盖"老表加列"与"重复执行不报错"
```

### B5 · 改了共享结构必须追踪全部调用方

以下位置是全仓库共享结构。改动前**必须 grep 全部调用方**并在 PR 里说明影响范围：

| 区域 | 影响面 |
|---|---|
| `app/constants.py` | 阶段序、门禁产出物、业务阈值 —— 被 services / api / web / tests 全面引用 |
| `app/services/gate.py` | 阶段推进的唯一入口，改错会让**所有项目**的门禁失效 |
| `app/services/permission.py` | 行级权限，改错静默泄露数据 |
| `app/models/*` | 建表结构，已存在的库不会自动迁移 |
| `app/api/serializers.py` | 所有接口的响应结构，前端与测试都依赖 |
| `app/web/pages.py::_plain_keyed` | 模板渲染枚举行为的总开关 |

**判定**：diff 触及上表却**没有**相应的调用方更新、也没有影响面说明 → **🔴**（"改了语义但调用方未同步"是最常见的一种）。

---

## 2. 建议项

### A1 · 业务阈值不要写死在业务代码里

阈值集中在 `app/constants.py`（如 `STAGE_STAGNANT_FACTOR`、`CHECKIN_EARLY_TOLERANCE_MIN`、`MAX_UPLOAD_BYTES`）与 `app/config.py`（可由环境变量覆盖）。

业务代码里出现新的魔法数字 → `🟡`。硬编码会让测试无法按用例覆盖，也让需求文档与代码加速漂移。

### A2 · 新增校验规则必须登记编号

校验规则一律编号（R-01…R-13 属拜访 `services/visit.py`，R-20…R-32 属项目 `services/project.py`），
**错误响应的 `code` 就是规则编号**——测试断言 `code` 而不是中文文案，文案会改、编号是契约。

新增一条校验却没有分配编号，或 `code` 与编号不符 → `🟡`。

### A3 · 新增用户可见行为必须补测试

按改动位置选**最小必要**测试集，不要每次都跑全套：

| 改动 | 必跑 |
|---|---|
| 纯函数 / 规则判定 | `pytest tests/unit` |
| 接口、序列化、权限 | `pytest tests/api` |
| 模板、页面路由、表单 | `pytest tests/api/test_form_contract.py tests/api/test_pages.py tests/api/test_render_contract.py` |
| 前端交互、登录态、重定向 | `pytest tests/e2e -m e2e` |
| 交付前 | `python ci/run_pipeline.py` |

**跑之前先确认测试环境未被污染**：`tests/conftest.py` 顶部有自检断言，
若 `CRM_PBKDF2_ITERATIONS` 未生效，套件会从几十秒膨胀到十几分钟。
该断言的意义就是让这类问题在收集阶段就炸出来——**不要建议绕过它，不要建议删掉它**。

### A4 · E2E 的两条硬规则

1. **不要用 `wait_for_load_state("networkidle")` 等表单提交** —— 点击前页面通常已处于 networkidle，该调用立即返回，断言落在旧页面。统一用 `CrmPage.submit(...)`（内部 `expect_navigation`）。
2. **提交被拒后页面会整页重载、所有表单字段被清空**。"先提交失败 → 再补一个字段 → 再提交"必须把之前的字段全部重填，否则会拿到误导性的必填错误。

---

## 3. 范围纪律（审查者也要遵守）

这一条既约束改代码的人，也约束审查者：

- 只审查本次 diff 触及的页面 / 模块。**不要要求顺手重构无关模块。**
- 尤其**不要建议重构 `app/services/` 下的规则函数**——它们被单元测试逐条钉死，
  重构会引起大量测试失败，反而掩盖真正的改动。
- 一次 PR 只关注一件事。若 diff 混入了与标题无关的改动，标 `🟡` 并建议拆开，而不是逐条深挖。
- 发现既有缺陷标 `🟣` 并另开 Issue 建议，不要在本 PR 里要求一并修复。

---

## 4. 审查结论需要的证据

给出 `🔴` 前，至少满足其一：

- 指向一个可复现的触发路径（页面操作顺序 或 请求样例 → 错误结果）
- 指向一段本次 diff 新增的代码 + 说明它绕过了哪条既有约定

给出「测试应该补」的建议时，指明应落在哪个层级目录与哪个测试文件。

不必要求审查者实跑全量流水线；但若 PR 未附本地结果，可提示：
```bash
python ci/run_pipeline.py --no-docker    # lint → unit → api → e2e
```
失败时完整日志在 `ci-logs/<阶段>.log`，结构化报告在 `ci-report.json`。

---

## 5. 明确不要报的内容

以下都不构成审查意见，报出来只会稀释真正的问题：

- 命名、空行、行宽等纯风格问题 —— 已由 `ruff` 在 `lint` 阶段强制
- "可以更抽象/加一层封装"这类可扩展性偏好
- README / 注释未同步（除非是接口契约变更导致文档**错误**）
- 依赖版本升级建议（有独立流程，不在代码审查内）
- "建议加个更全的测试" —— 除非能指出**具体缺少哪条用例、会导致什么漏检**
