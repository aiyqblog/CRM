"""组织与用户。

角色决定权限矩阵中的行，team_id / manager_id 决定数据可见范围。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants import Role
from app.models.base import Base, FKType, PKType, utcnow


class Team(Base):
    """销售团队。支持多级组织架构（parent_id 自引用）。"""

    __tablename__ = "team"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(FKType, ForeignKey("team.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    children: Mapped[list["Team"]] = relationship(
        "Team", back_populates="parent", cascade="all"
    )
    parent: Mapped["Team | None"] = relationship(
        "Team", back_populates="children", remote_side="Team.id"
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Team {self.id} {self.name}>"


class User(Base):
    """系统用户。

    ``role`` 使用字符串存储枚举值，避免数据库枚举类型的迁移麻烦。
    ``password_hash`` 由 app.services.auth 负责生成与校验。
    """

    __tablename__ = "crm_user"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=Role.SALES.value)
    team_id: Mapped[int | None] = mapped_column(FKType, ForeignKey("team.id"), nullable=True)
    manager_id: Mapped[int | None] = mapped_column(FKType, ForeignKey("crm_user.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    team: Mapped["Team | None"] = relationship("Team", foreign_keys=[team_id])
    manager: Mapped["User | None"] = relationship(
        "User", remote_side="User.id", foreign_keys=[manager_id]
    )

    @property
    def role_enum(self) -> Role:
        return Role(self.role)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<User {self.id} {self.username} {self.role}>"
