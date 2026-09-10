"""ORM 模型包。

此处集中导入，确保 ``Base.metadata`` 在 ``init_db()`` 前已注册全部表。
"""

from app.models.base import Base, FKType, PKType, utcnow
from app.models.customer import Customer, CustomerReport
from app.models.project import (
    ProjectGateItem,
    ProjectStageHistory,
    ProjectVisitRel,
    SalesProject,
)
from app.models.user import Team, User
from app.models.visit import VisitAttachment, VisitPlan, VisitRecord

__all__ = [
    "Base",
    "PKType",
    "FKType",
    "utcnow",
    "Team",
    "User",
    "Customer",
    "CustomerReport",
    "VisitPlan",
    "VisitRecord",
    "VisitAttachment",
    "SalesProject",
    "ProjectStageHistory",
    "ProjectGateItem",
    "ProjectVisitRel",
]
