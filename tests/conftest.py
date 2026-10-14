# -*- coding: utf-8 -*-
"""Test setup: the real Flask app with a throw-away data folder, a fake Zabbix
API (an old and a new version) and a fake SMTP server. Nothing leaves the machine."""
import os
import sys
import tempfile
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Must be set before app.py is imported: it reads them at import time.
os.environ["ZRS_DATA_DIR"] = tempfile.mkdtemp(prefix="zrs-tests-")
os.environ["ZRS_NO_SCHEDULER"] = "1"
os.environ["ZABBIX_URL"] = ""          # no default server seeded for admin
os.environ["SECRET_KEY"] = "tests"

import app as zrs                      # noqa: E402
from fakes import PASSWORD, FakeSmtp, FakeZabbix   # noqa: E402

zrs.create_admin()


@pytest.fixture(scope="session")
def zabbix_old():
    """Zabbix 6.0: the token is accepted only in the request body."""
    return FakeZabbix("6.0.0")


@pytest.fixture(scope="session")
def zabbix_new():
    """Zabbix 7.2: the token is accepted only as a Bearer header."""
    return FakeZabbix("7.2.1")


@pytest.fixture()
def smtp():
    return FakeSmtp()


@pytest.fixture(autouse=True)
def clean_state():
    """Each test starts with an empty database (except the admin account),
    default business hours and no sign-in failures on record."""
    with zrs.app.app_context():
        for model in (zrs.ReportSchedule, zrs.ZabbixServer, zrs.SmtpConfig, zrs.BusinessHours, zrs.NobetConfig):
            model.query.delete()
        zrs.User.query.filter(zrs.User.username != "admin").delete()
        admin = zrs.User.query.filter_by(username="admin").first()
        admin.set_password("AdminPass99")
        admin.must_change_password = False
        zrs.sdb.session.commit()
        zrs._business_hours.update(zrs.DEFAULT_BUSINESS_HOURS)
    zrs._failed_logins.clear()
    zrs.cache_db.clear_cache()
    yield


def add_user(username, password="UserPass99", role="user", must_change=False):
    with zrs.app.app_context():
        user = zrs.User(username=username, role=role, must_change_password=must_change)
        user.set_password(password)
        zrs.sdb.session.add(user)
        zrs.sdb.session.commit()
        return user.id


def sign_in(username="admin", password="AdminPass99"):
    """A test client signed in as the given user."""
    client = zrs.app.test_client()
    response = client.post("/login", data={"username": username, "password": password})
    assert response.status_code == 302, response.get_data(as_text=True)[:200]
    return client


def add_server(client, fake, name="Fake", activate=True, **extra):
    body = dict({"name": name, "url": fake.url, "username": "api", "password": PASSWORD}, **extra)
    server_id = client.post("/api/servers", json=body).get_json()["id"]
    if activate:
        assert client.post(f"/api/servers/{server_id}/activate").get_json()["success"]
    return server_id


def run_report(client, days=10, groups=()):
    """Start an alarm report job for the last `days` days and wait for its rows."""
    session_id = client.post("/alarm/api/auto-login", json={}).get_json()["sessionId"]
    now = int(time.time())
    job_id = client.post("/alarm/api/report-data", json={
        "sessionId": session_id, "timeFrom": now - days * 86400, "timeTill": now,
        "groupIds": list(groups)}).get_json()["jobId"]
    for _ in range(240):
        progress = client.post("/alarm/api/report-progress", json={"jobId": job_id}).get_json()
        if progress["status"] in ("done", "error"):
            break
        time.sleep(0.25)
    result = client.post("/alarm/api/report-result", json={"jobId": job_id}).get_json()
    assert result["success"], result
    return job_id, result["data"]
