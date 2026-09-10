"""终端客户报备测试（需求文档 4.7 / R-27）。

渠道冲突是模组行业最容易伤到代理商关系的环节，
所以「绝不自动覆盖」这条设计必须有测试兜住。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import report as report_svc

pytestmark = pytest.mark.api


def make_end_customer(client, name="终端品牌客户"):
    return client.post(
        "/api/customers",
        json={"name": name, "industry": "终端品牌", "level": "A"},
    ).json()


def make_project(client, customer_id, name="渠道项目"):
    return client.post(
        "/api/projects", json={"project_name": name, "customer_id": customer_id}
    ).json()


def test_create_report(as_sales, customer_id):
    end_customer = make_end_customer(as_sales)
    project = make_project(as_sales, customer_id)

    response = as_sales.post(
        f"/api/projects/{project['id']}/reports",
        json={"end_customer_id": end_customer["id"]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == 1
    assert body["is_locked"] is False

    start = date.fromisoformat(body["start_date"])
    expire = date.fromisoformat(body["expire_date"])
    assert (expire - start).days == 90


def test_duplicate_report_same_project_rejected(as_sales, customer_id):
    end_customer = make_end_customer(as_sales)
    project = make_project(as_sales, customer_id)
    payload = {"end_customer_id": end_customer["id"]}

    assert (
        as_sales.post(f"/api/projects/{project['id']}/reports", json=payload).status_code
        == 201
    )
    second = as_sales.post(f"/api/projects/{project['id']}/reports", json=payload)
    assert second.status_code == 409
    assert second.json()["code"] == "R-27"


def test_conflicting_report_from_another_project_escalates(
    as_sales, as_sales_same_team, customer_id, fresh_database
):
    """R-27：被他方报备时进入裁决队列，绝不自动覆盖。"""
    peer_customer = fresh_database["customers"]["CUS-HZ-004"]

    end_customer = make_end_customer(as_sales, "共享终端客户")
    first = make_project(as_sales, customer_id, "先报备的项目")
    second = make_project(as_sales_same_team, peer_customer, "后报备的项目")

    as_sales.post(
        f"/api/projects/{first['id']}/reports",
        json={"end_customer_id": end_customer["id"]},
    )

    response = as_sales_same_team.post(
        f"/api/projects/{second['id']}/reports",
        json={"end_customer_id": end_customer["id"]},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "R-27"
    assert body["details"]["action"] == "escalate"
    assert body["details"]["conflict_project_id"] == first["id"]


def test_lock_on_design_in_stage(as_sales, customer_id, db):
    """进入 Design-in 后报备转锁定，不再自动释放。"""
    from app.constants import ProjectStage

    end_customer = make_end_customer(as_sales)
    project = make_project(as_sales, customer_id)
    report = as_sales.post(
        f"/api/projects/{project['id']}/reports",
        json={"end_customer_id": end_customer["id"]},
    ).json()

    from app.models import SalesProject

    row = db.get(SalesProject, project["id"])
    row.stage = ProjectStage.DESIGN_IN.value
    db.commit()

    entry = report_svc.get_report_or_404(db, report["id"])
    report_svc.lock_report(db, report=entry, project=row)
    db.commit()

    detail = as_sales.get(f"/api/projects/{project['id']}").json()
    assert detail["end_customer_locked"] is True


def test_release_expired_reports(as_sales, customer_id, db):
    end_customer = make_end_customer(as_sales)
    project = make_project(as_sales, customer_id)
    report = as_sales.post(
        f"/api/projects/{project['id']}/reports",
        json={"end_customer_id": end_customer["id"]},
    ).json()

    from app.models import CustomerReport

    row = db.get(CustomerReport, report["id"])
    row.expire_date = date.today() - timedelta(days=1)
    db.commit()

    released = report_svc.release_expired(db)
    db.commit()
    assert released == 1

    refreshed = db.get(CustomerReport, report["id"])
    assert refreshed.status == CustomerReport.STATUS_RELEASED


def test_locked_report_not_released(as_sales, customer_id, db):
    """已锁定的报备即使过期也不释放 —— 锁定意味着项目已进入攻坚期。"""
    end_customer = make_end_customer(as_sales)
    project = make_project(as_sales, customer_id)
    report = as_sales.post(
        f"/api/projects/{project['id']}/reports",
        json={"end_customer_id": end_customer["id"]},
    ).json()

    from app.models import CustomerReport

    row = db.get(CustomerReport, report["id"])
    row.expire_date = date.today() - timedelta(days=1)
    row.is_locked = True
    db.commit()

    assert report_svc.release_expired(db) == 0
    db.commit()

    refreshed = db.get(CustomerReport, report["id"])
    assert refreshed.status == CustomerReport.STATUS_ACTIVE


def test_report_list(as_sales, customer_id):
    end_customer = make_end_customer(as_sales)
    project = make_project(as_sales, customer_id)
    as_sales.post(
        f"/api/projects/{project['id']}/reports",
        json={"end_customer_id": end_customer["id"]},
    )

    response = as_sales.get(f"/api/projects/{project['id']}/reports")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


def test_report_unknown_end_customer(as_sales, customer_id):
    project = make_project(as_sales, customer_id)
    response = as_sales.post(
        f"/api/projects/{project['id']}/reports", json={"end_customer_id": 999999}
    )
    assert response.status_code == 404


def test_is_report_effective_after_expiry():
    from app.models import CustomerReport

    report = CustomerReport(
        end_customer_id=1,
        project_id=1,
        owner_id=1,
        start_date=date(2026, 1, 1),
        expire_date=date(2026, 3, 1),
        is_locked=False,
        status=CustomerReport.STATUS_ACTIVE,
    )
    assert report_svc.is_report_effective(report, on_date=date(2026, 2, 1)) is True
    assert report_svc.is_report_effective(report, on_date=date(2026, 4, 1)) is False

    report.is_locked = True
    assert report_svc.is_report_effective(report, on_date=date(2027, 1, 1)) is True
