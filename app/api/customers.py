"""客户接口。"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.api.schemas import CustomerIn
from app.api.serializers import customer_out, project_out, record_out
from app.models import Customer
from app.models.base import utcnow
from app.services import permission
from app.services.errors import NotFound, ValidationFailed

router = APIRouter(prefix="/api/customers", tags=["customers"])


def get_customer_checked(db, user, customer_id: int) -> Customer:
    """取客户并校验数据权限。

    所有按 id 访问客户的接口都必须经过这里 —— 直接 ``db.get`` 是最典型的
    越权漏洞来源（改 URL 参数就能看别人客户）。
    """
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise NotFound("客户不存在")

    scope = permission.resolve_scope(db, user)
    decision = permission.decide_read(user, customer.owner_id, scope)
    decision.raise_if_denied()
    return customer


@router.get("")
def list_customers(
    user: CurrentUser,
    db: DbSession,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    scope = permission.resolve_scope(db, user)

    conditions = []
    if not scope.is_full:
        conditions.append(Customer.owner_id.in_(list(scope.visible_user_ids or [])))
    if keyword:
        conditions.append(Customer.name.like(f"%{keyword}%"))

    rows = (
        db.execute(
            select(Customer)
            .where(*conditions)
            .order_by(Customer.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return {"items": [customer_out(c) for c in rows], "total": len(rows)}


@router.post("", status_code=201)
def create_customer(payload: CustomerIn, user: CurrentUser, db: DbSession) -> dict:
    if not permission.can_create(user):
        raise ValidationFailed("PERM-403", "当前角色不允许新建客户")

    customer = Customer(
        **payload.model_dump(),
        owner_id=user.id,
        team_id=user.team_id,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer_out(customer)


@router.get("/{customer_id}")
def get_customer(customer_id: int, user: CurrentUser, db: DbSession) -> dict:
    return customer_out(get_customer_checked(db, user, customer_id))


@router.patch("/{customer_id}")
def update_customer(
    customer_id: int, payload: CustomerIn, user: CurrentUser, db: DbSession
) -> dict:
    customer = get_customer_checked(db, user, customer_id)
    if not permission.can_create(user):
        raise ValidationFailed("PERM-403", "当前角色不允许修改客户")

    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(customer, key, value)
    customer.updated_by = user.id
    db.commit()
    db.refresh(customer)
    return customer_out(customer)


@router.get("/{customer_id}/timeline")
def customer_timeline(customer_id: int, user: CurrentUser, db: DbSession) -> dict:
    """客户 360° 视图：聚合该客户的拜访记录与销售项目（需求文档 5.2）。"""
    from app.models import SalesProject, VisitRecord

    customer = get_customer_checked(db, user, customer_id)
    scope = permission.resolve_scope(db, user)

    visit_conditions = [
        VisitRecord.customer_id == customer.id,
        VisitRecord.is_deleted == 0,
    ]
    project_conditions = [SalesProject.customer_id == customer.id]
    if not scope.is_full:
        visit_conditions.append(
            VisitRecord.owner_id.in_(list(scope.visible_user_ids or []))
        )
        project_conditions.append(
            SalesProject.owner_id.in_(list(scope.visible_user_ids or []))
        )

    visits = (
        db.execute(
            select(VisitRecord)
            .where(*visit_conditions)
            .order_by(VisitRecord.checkin_time.desc())
        )
        .scalars()
        .all()
    )
    projects = (
        db.execute(
            select(SalesProject)
            .where(*project_conditions)
            .order_by(SalesProject.updated_at.desc())
        )
        .scalars()
        .all()
    )

    return {
        "customer": customer_out(customer),
        "visits": [record_out(v, with_attachments=False) for v in visits],
        "projects": [project_out(p) for p in projects],
        "generated_at": utcnow().isoformat(),
    }
