"""种子数据。

供本地开发、接口测试与 E2E 测试共用。**E2E 与接口测试必须用同一套账号**，
否则 E2E 里出现的登录失败会被误当成前端 bug 去排查，浪费大量时间。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.constants import Role
from app.models import Customer, SalesProject, Team, User
from app.services import auth
from app.services import project as project_svc

#: 测试与开发共用的固定账号
SEED_PASSWORD = "crm123456"

USERS = [
    # username, display_name, role, team_key, manager_username
    ("admin", "系统管理员", Role.ADMIN, "hq", None),
    ("director", "季总监", Role.SALES_DIRECTOR, "hq", None),
    ("manager_a", "张主管", Role.SALES_MANAGER, "team_a", "director"),
    ("manager_b", "李主管", Role.SALES_MANAGER, "team_b", "director"),
    ("sales_a", "王销售", Role.SALES, "team_a", "manager_a"),
    ("sales_a2", "赵销售", Role.SALES, "team_a", "manager_a"),
    ("sales_b", "孙销售", Role.SALES, "team_b", "manager_b"),
    ("tech_a", "周技术", Role.TECH_SUPPORT, "team_a", "manager_a"),
    ("market", "吴市场", Role.MARKETING, "hq", "director"),
]

TEAMS = [
    ("hq", "总部", None),
    ("team_a", "华南销售一组", "hq"),
    ("team_b", "华东销售二组", "hq"),
]

CUSTOMERS = [
    {
        "name": "深圳华智终端有限公司",
        "code": "CUS-HZ-001",
        "industry": "智能穿戴",
        "level": "A",
        "address": "深圳市南山区科技园南区高新南七道 12 号",
        "longitude": 113.9432,
        "latitude": 22.5248,
        "contact_name": "刘经理",
        "contact_phone": "13800001111",
        "owner": "sales_a",
    },
    {
        "name": "广州云链物联科技",
        "code": "CUS-YL-002",
        "industry": "车联网",
        "level": "A",
        "address": "广州市黄埔区科学大道 182 号",
        "longitude": 113.4513,
        "latitude": 23.1795,
        "contact_name": "陈总",
        "contact_phone": "13900002222",
        "owner": "sales_a",
    },
    {
        "name": "苏州智造方案商",
        "code": "CUS-SZ-003",
        "industry": "工业网关",
        "level": "B",
        "address": "苏州市工业园区星湖街 328 号",
        "longitude": 120.7398,
        "latitude": 31.2795,
        "contact_name": "黄工",
        "contact_phone": "13700003333",
        "owner": "sales_b",
    },
    {
        "name": "杭州终端品牌（代理渠道）",
        "code": "CUS-HZ-004",
        "industry": "POS 机具",
        "level": "B",
        "address": "杭州市滨江区江南大道 588 号",
        "longitude": 120.2120,
        "latitude": 30.1880,
        "contact_name": "郑经理",
        "contact_phone": "13600004444",
        "owner": "sales_a2",
    },
]


def seed(db: Session, *, with_demo_data: bool = True) -> dict:
    """幂等地写入种子数据。已存在则直接返回既有账号。"""
    teams: dict[str, Team] = {}
    for key, name, _parent_key in TEAMS:
        team = db.query(Team).filter(Team.name == name).one_or_none()
        if team is None:
            team = Team(name=name)
            db.add(team)
            db.flush()
        teams[key] = team

    for key, _name, parent_key in TEAMS:
        if parent_key:
            teams[key].parent_id = teams[parent_key].id
    db.flush()

    users: dict[str, User] = {}
    for username, display_name, role, team_key, _manager_username in USERS:
        user = db.query(User).filter(User.username == username).one_or_none()
        if user is None:
            user = auth.create_user(
                db,
                username=username,
                password=SEED_PASSWORD,
                display_name=display_name,
                role=role.value,
                team_id=teams[team_key].id,
            )
        users[username] = user
    db.flush()

    for _username, _display, _role, _team, manager_username in USERS:
        if manager_username:
            users[_username].manager_id = users[manager_username].id
    db.flush()

    customers: dict[str, Customer] = {}
    if with_demo_data:
        for spec in CUSTOMERS:
            existing = db.query(Customer).filter(Customer.code == spec["code"]).one_or_none()
            if existing is not None:
                customers[spec["code"]] = existing
                continue
            owner = users[spec["owner"]]
            customer = Customer(
                name=spec["name"],
                code=spec["code"],
                owner_id=owner.id,
                team_id=owner.team_id,
                industry=spec["industry"],
                level=spec["level"],
                address=spec["address"],
                longitude=spec["longitude"],
                latitude=spec["latitude"],
                contact_name=spec["contact_name"],
                contact_phone=spec["contact_phone"],
                created_by=owner.id,
                updated_by=owner.id,
            )
            db.add(customer)
            db.flush()
            customers[spec["code"]] = customer

    db.commit()

    return {
        "users": {name: user.id for name, user in users.items()},
        "teams": {key: team.id for key, team in teams.items()},
        "customers": {code: c.id for code, c in customers.items()},
    }


def seed_demo_projects(db: Session, *, owner_username: str = "sales_a") -> list[int]:
    """造几个演示项目，覆盖不同阶段，便于看板与 E2E 使用。"""
    owner = db.query(User).filter(User.username == owner_username).one()
    customer = db.query(Customer).filter(Customer.code == "CUS-HZ-001").one()

    specs = [
        ("华智智能手表 AI 模组项目", 12.5, 200_000, 3),
        ("华智穿戴海外版模组", 11.8, 80_000, 2),
    ]

    created: list[int] = []
    for name, price, qty, years in specs:
        existing = (
            db.query(SalesProject).filter(SalesProject.project_name == name).one_or_none()
        )
        if existing is not None:
            created.append(existing.id)
            continue

        proj = project_svc.create_project(
            db,
            user=owner,
            project_name=name,
            customer=customer,
            unit_price=price,
            est_annual_qty=qty,
            lifecycle_years=years,
        )
        created.append(proj.id)

    db.commit()
    return created


if __name__ == "__main__":  # pragma: no cover - 供本地手动执行
    from app.database import SessionLocal, init_db

    init_db()
    with SessionLocal() as session:
        info = seed(session)
        project_ids = seed_demo_projects(session)
        print("种子数据写入完成")
        print("  用户:", info["users"])
        print("  客户:", info["customers"])
        print("  项目:", project_ids)
        print(f"  统一口令: {SEED_PASSWORD}")
