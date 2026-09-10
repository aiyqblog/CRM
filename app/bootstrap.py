"""首次启动初始化。

容器启动时调用：建表，并按需灌入演示数据。
幂等 —— 重复执行不会产生重复数据（seed 内部按唯一键判断）。
"""

from __future__ import annotations

import json
import os

from app.database import SessionLocal, init_db


def ensure_initialized() -> dict:
    init_db()

    should_seed = os.environ.get("CRM_SEED_DEMO", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not should_seed:
        return {"initialized": True, "seeded": False}

    from app.seed import seed, seed_demo_projects

    with SessionLocal() as session:
        info = seed(session)
        project_ids = seed_demo_projects(session)

    return {
        "initialized": True,
        "seeded": True,
        "users": len(info["users"]),
        "customers": len(info["customers"]),
        "projects": len(project_ids),
    }


if __name__ == "__main__":
    print(json.dumps(ensure_initialized(), ensure_ascii=False))
