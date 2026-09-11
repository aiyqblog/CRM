"""领域常量：角色、状态、阶段、门禁产出物。

本模块是需求说明书第 3、4 章的可执行版本。任何业务阈值调整都应先改这里，
再同步更新需求文档，避免代码与文档漂移。
"""

from __future__ import annotations

from enum import Enum


# --------------------------------------------------------------------------
# 角色
# --------------------------------------------------------------------------
class Role(str, Enum):
    """系统角色。数据权限按「本人 + 团队 + 组织架构」三层展开。"""

    SALES = "sales"                  # 销售（本人）
    SALES_MANAGER = "sales_manager"  # 销售主管
    SALES_DIRECTOR = "sales_director"  # 销售总监
    TECH_SUPPORT = "tech_support"    # 技术支持
    MARKETING = "marketing"          # 市场 / 产品（只读聚合）
    ADMIN = "admin"                  # 系统管理员


ROLE_LABELS: dict[Role, str] = {
    Role.SALES: "销售",
    Role.SALES_MANAGER: "销售主管",
    Role.SALES_DIRECTOR: "销售总监",
    Role.TECH_SUPPORT: "技术支持",
    Role.MARKETING: "市场/产品",
    Role.ADMIN: "系统管理员",
}


# --------------------------------------------------------------------------
# 拜访：类型与状态
# --------------------------------------------------------------------------
class VisitType(int, Enum):
    FIRST = 1        # 首次拜访
    ROUTINE = 2      # 例行拜访
    TECH_SUPPORT = 3  # 技术支持
    NEGOTIATION = 4  # 商务谈判
    COMPLAINT = 5    # 客诉处理


VISIT_TYPE_LABELS: dict[VisitType, str] = {
    VisitType.FIRST: "首次拜访",
    VisitType.ROUTINE: "例行拜访",
    VisitType.TECH_SUPPORT: "技术支持",
    VisitType.NEGOTIATION: "商务谈判",
    VisitType.COMPLAINT: "客诉处理",
}


class PlanStatus(int, Enum):
    """拜访计划状态（需求文档 3.4 状态机）。"""

    PENDING = 1    # 待拜访
    ONGOING = 2    # 进行中
    COMPLETED = 3  # 已完成
    CANCELLED = 4  # 已取消


PLAN_STATUS_LABELS: dict[PlanStatus, str] = {
    PlanStatus.PENDING: "待拜访",
    PlanStatus.ONGOING: "进行中",
    PlanStatus.COMPLETED: "已完成",
    PlanStatus.CANCELLED: "已取消",
}


class RecordStatus(int, Enum):
    """拜访记录状态。"""

    ONGOING = 2        # 进行中（已签到未签退）
    COMPLETED = 3      # 已完成
    AUTO_CLOSED = 5    # 已自动关闭（超时 24h）


RECORD_STATUS_LABELS: dict[RecordStatus, str] = {
    RecordStatus.ONGOING: "进行中",
    RecordStatus.COMPLETED: "已完成",
    RecordStatus.AUTO_CLOSED: "已自动关闭",
}


class LocationStatus(int, Enum):
    """定位状态（R-03 / R-06 / R-07）。"""

    NORMAL = 1          # 正常
    OUT_OF_FENCE = 2    # 超出范围
    FAILED = 3          # 定位失败
    MOCKED = 4          # 使用模拟位置


LOCATION_STATUS_LABELS: dict[LocationStatus, str] = {
    LocationStatus.NORMAL: "正常",
    LocationStatus.OUT_OF_FENCE: "超出范围",
    LocationStatus.FAILED: "定位失败",
    LocationStatus.MOCKED: "模拟位置",
}


class SyncStatus(int, Enum):
    """离线补传状态（R-10）。"""

    SYNCED = 1    # 已同步
    PENDING = 2   # 待同步


class FileType(int, Enum):
    IMAGE = 1
    VOICE = 2
    VIDEO = 3
    DOC = 4


# --------------------------------------------------------------------------
# 销售项目：8 阶段模型（需求文档 4.2）
# --------------------------------------------------------------------------
class ProjectStage(str, Enum):
    OPPORTUNITY = "opportunity"          # 机会识别
    TECH_EVAL = "tech_eval"              # 技术评估
    SAMPLE_TEST = "sample_test"          # 送样测试
    DESIGN_IN = "design_in"              # Design-in
    PILOT_RUN = "pilot_run"              # 小批量试产
    NEGOTIATION = "negotiation"          # 商务谈判
    MASS_PRODUCTION = "mass_production"  # 签约量产
    WON = "won"                          # 赢单


#: 阶段顺序。推进 = 索引 +1，回退 = 索引 -1。
STAGE_ORDER: list[ProjectStage] = [
    ProjectStage.OPPORTUNITY,
    ProjectStage.TECH_EVAL,
    ProjectStage.SAMPLE_TEST,
    ProjectStage.DESIGN_IN,
    ProjectStage.PILOT_RUN,
    ProjectStage.NEGOTIATION,
    ProjectStage.MASS_PRODUCTION,
    ProjectStage.WON,
]

STAGE_LABELS: dict[ProjectStage, str] = {
    ProjectStage.OPPORTUNITY: "机会识别",
    ProjectStage.TECH_EVAL: "技术评估",
    ProjectStage.SAMPLE_TEST: "送样测试",
    ProjectStage.DESIGN_IN: "Design-in",
    ProjectStage.PILOT_RUN: "小批量试产",
    ProjectStage.NEGOTIATION: "商务谈判",
    ProjectStage.MASS_PRODUCTION: "签约量产",
    ProjectStage.WON: "赢单",
}

#: 赢单概率由阶段自动带出，不允许人工填写（需求文档 4.2）。
STAGE_WIN_RATE: dict[ProjectStage, int] = {
    ProjectStage.OPPORTUNITY: 10,
    ProjectStage.TECH_EVAL: 20,
    ProjectStage.SAMPLE_TEST: 40,
    ProjectStage.DESIGN_IN: 60,
    ProjectStage.PILOT_RUN: 75,
    ProjectStage.NEGOTIATION: 85,
    ProjectStage.MASS_PRODUCTION: 95,
    ProjectStage.WON: 100,
}

#: 典型周期（天），用于 R-28 停滞判定。
STAGE_TYPICAL_DAYS: dict[ProjectStage, int] = {
    ProjectStage.OPPORTUNITY: 14,
    ProjectStage.TECH_EVAL: 42,
    ProjectStage.SAMPLE_TEST: 84,
    ProjectStage.DESIGN_IN: 112,
    ProjectStage.PILOT_RUN: 84,
    ProjectStage.NEGOTIATION: 42,
    ProjectStage.MASS_PRODUCTION: 28,
    ProjectStage.WON: 0,
}

#: 停滞阈值倍数：停留 > 典型周期 × 1.5 即标记停滞（R-28）。
STAGE_STAGNANT_FACTOR = 1.5


class ChannelType(int, Enum):
    DIRECT = 1    # 直销
    AGENT = 2     # 代理
    SOLUTION = 3  # 方案商


CHANNEL_TYPE_LABELS: dict[ChannelType, str] = {
    ChannelType.DIRECT: "直销",
    ChannelType.AGENT: "代理",
    ChannelType.SOLUTION: "方案商",
}


class ProjectType(int, Enum):
    NEW_DESIGN_IN = 1  # 新项目 design-in
    REPLACE = 2        # 替换竞品
    EXPANSION = 3      # 扩容
    MAINTENANCE = 4    # 维护


PROJECT_TYPE_LABELS: dict[ProjectType, str] = {
    ProjectType.NEW_DESIGN_IN: "新项目 Design-in",
    ProjectType.REPLACE: "替换竞品",
    ProjectType.EXPANSION: "扩容",
    ProjectType.MAINTENANCE: "维护",
}


class ProjectCategory(int, Enum):
    """项目类别（Issue #5）。

    注意与 :class:`ProjectType` 区分：项目类型说的是「这个项目在干什么」
    （新导入 / 替换竞品 / 扩容 / 维护），项目类别说的是「这个项目多大」。
    两者是独立维度，不是同一个字段的别名。
    """

    LARGE = 1  # 大型项目
    SMALL = 2  # 小型项目


PROJECT_CATEGORY_LABELS: dict[ProjectCategory, str] = {
    ProjectCategory.LARGE: "大型项目",
    ProjectCategory.SMALL: "小型项目",
}

#: 新建项目时表单默认选中的类别；也是存量数据回填用的值（Issue #5）。
DEFAULT_PROJECT_CATEGORY = ProjectCategory.SMALL

#: 产品系列 → 可选型号（Issue #5）。R-31：型号必须隶属所选系列。
#: 这里是唯一数据源 —— 前端联动下拉与服务端校验都读它，避免两处清单漂移。
PRODUCT_SERIES_MODELS: dict[str, tuple[str, ...]] = {
    "5G/4G蜂窝天线": (
        "YECT005W1A",
        "YECT004W1A",
        "YECT028W1A",
        "YECT003W1A",
    ),
    "GNSS定位天线": (
        "YFGC007E3A",
        "YFGD000AA",
        "YFGD000BA",
        "YEGB000Q1A",
        "YEGN000Q1A",
        "YEGT000W8A",
    ),
    "卫星通信天线": (
        "YFTA009E3AM",
        "YEGM023AA",
    ),
    "Wi-Fi/蓝牙/LoRa天线": (
        "YFNF868F3AM",
        "YFNF915F3AM",
        "YFBC001WWA",
    ),
}


class CloseType(int, Enum):
    WON = 1      # 赢单
    LOST = 2     # 输单
    SHELVED = 3  # 搁置


CLOSE_TYPE_LABELS: dict[CloseType, str] = {
    CloseType.WON: "赢单",
    CloseType.LOST: "输单",
    CloseType.SHELVED: "搁置",
}


class ProjectStatus(int, Enum):
    ONGOING = 1  # 进行中
    CLOSED = 2   # 已关闭


class StageAction(int, Enum):
    """阶段历史中的动作类型。"""

    ADVANCE = 1  # 推进
    ROLLBACK = 2  # 回退
    CLOSE = 3    # 关闭


class GateStatus(int, Enum):
    NOT_SUBMITTED = 0  # 未提交
    SUBMITTED = 1      # 已提交
    CONFIRMED = 2      # 已确认


# --------------------------------------------------------------------------
# 阶段产出物清单（需求文档 4.4）
#: {阶段: [(编码, 名称, 是否必需), ...]}
#: 语义：完成某阶段时需齐备的产出物；推进出该阶段时校验（R-20）。
# --------------------------------------------------------------------------
GATE_ITEMS: dict[ProjectStage, list[tuple[str, str, bool]]] = {
    ProjectStage.OPPORTUNITY: [
        ("G1-01", "客户需求说明", True),
        ("G1-02", "客户基本信息表", False),
    ],
    ProjectStage.TECH_EVAL: [
        ("G2-01", "技术方案书", True),
        ("G2-02", "选型确认记录", True),
        ("G2-03", "竞品对标分析", False),
    ],
    ProjectStage.SAMPLE_TEST: [
        ("G3-01", "送样申请单", True),
        ("G3-02", "送样签收记录", True),
        ("G3-03", "客户测试反馈", True),
    ],
    ProjectStage.DESIGN_IN: [
        ("G4-01", "客户立项通知", True),
        ("G4-02", "DV 测试报告", True),
    ],
    ProjectStage.PILOT_RUN: [
        ("G5-01", "试产报告", True),
        ("G5-02", "小批量订单", True),
    ],
    ProjectStage.NEGOTIATION: [
        ("G6-01", "报价单", True),
        ("G6-02", "商务条款确认书", True),
    ],
    ProjectStage.MASS_PRODUCTION: [
        ("G7-01", "正式合同", True),
        ("G7-02", "量产订单", True),
    ],
    ProjectStage.WON: [],
}

#: 编码 -> 名称，便于日志与报错信息展示。
GATE_ITEM_NAMES: dict[str, str] = {
    code: name for items in GATE_ITEMS.values() for code, name, _ in items
}


# --------------------------------------------------------------------------
# 终端客户报备（需求文档 4.7）
# --------------------------------------------------------------------------
DEFAULT_REPORT_VALID_DAYS = 90     # 报备默认有效期
REPORT_RENEW_DAYS = 90             # 续期时长
#: 报备在项目进入该阶段后转为锁定，不再自动释放。
REPORT_LOCK_STAGE = ProjectStage.DESIGN_IN


# --------------------------------------------------------------------------
# 业务阈值（需求文档 3.6 校验规则，可由配置覆盖）
# --------------------------------------------------------------------------
CHECKIN_EARLY_TOLERANCE_MIN = 30   # R-01：签到不得早于计划开始前 30 分钟
DEFAULT_FENCE_METERS = 1000        # R-03：超出该距离标记「超出范围」
MIN_VISIT_MINUTES = 5              # R-05：低于该时长标记异常
MAX_IMAGES_PER_RECORD = 9          # R-08
MAX_VOICE_PER_RECORD = 1           # R-08
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # R-08：单文件 20MB
COMPRESSED_IMAGE_BYTES = 500 * 1024  # R-09：压缩目标 500KB
MIN_CONTENT_CHARS = 20             # R-11：纪要至少 20 字
OFFLINE_TIME_DRIFT_MIN = 30        # R-10：设备时间与服务端偏差阈值
EDIT_WINDOW_HOURS = 24             # R-12：本人可编辑/删除窗口
AUTO_CLOSE_HOURS = 24              # 状态机：超时未签退自动关闭
AMOUNT_CHANGE_THRESHOLD = 0.20     # R-26：金额变更超过 20% 需审批
OVERRIDE_REASON_MIN_CHARS = 30     # R-21：门禁覆盖理由至少 30 字
ROLLBACK_REASON_MIN_CHARS = 20     # R-22：回退原因至少 20 字
