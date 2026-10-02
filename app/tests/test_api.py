from fastapi.testclient import TestClient

from app.api import deps
from app.api.main import app
from app.config import Settings, get_settings
from app.db.memory import InMemoryDB
from app.tests.conftest import FakeStore


def test_health_needs_no_auth_or_db():
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["display_timezone"] == "America/Chicago"


def test_controls_default_paused_and_locked(client):
    body = client.get("/controls/status").json()
    assert body["mode"] == "PAUSED"
    assert body["signal_sending_enabled"] is False
    assert body["execution_code_present"] is False
    assert body["state_source"] == "database"
    assert body["prohibited_capabilities"]


def test_controls_fail_safe_when_row_missing(client, store):
    store.data["system_control_state"] = []
    body = client.get("/controls/status").json()
    assert body["mode"] == "PAUSED" and body["state_source"] == "default"


def test_research_status(client):
    body = client.get("/research/status").json()
    assert body["counts"]["peer_pairs"] == 3
    assert body["pairs_by_status"] == {"CANDIDATE": 2, "ACTIVE_MANUALLY": 1}
    assert body["latest_packet_at"].startswith("2026-09-30")
    assert any("PAUSED" in n for n in body["notes"])


def test_data_quality_only_open_issues(client):
    body = client.get("/data-quality").json()
    assert body["open_issue_count"] == 2
    assert body["by_severity"] == {"ERROR": 1, "WARNING": 1}
    assert body["issues"][0]["issue_type"] == "MISSING_QUOTE"  # newest first


def test_manual_portfolio_cost_basis(client):
    body = client.get("/portfolio/manual").json()
    pf = body["portfolios"][0]
    assert pf["cost_basis_gross_exposure_usd"] == 12000.0  # 6000 long + 6000 short
    assert pf["cost_basis_net_exposure_usd"] == 0.0
    assert "never places" in body["disclaimer"]


def test_score_is_estimate_only(client):
    body = client.get("/portfolio/score").json()
    assert body["official"] is False and "ESTIMATE" in body["disclaimer"]
    assert body["scores"][0]["latest"] is None


def test_only_refresh_and_research_review_routes_are_non_get():
    paths = app.openapi()["paths"]
    non_get = {(p, m) for p, ops in paths.items() for m in ops if m != "get"}
    assert non_get == {
        ("/admin/refresh/{job_name}", "post"),
        ("/admin/refresh-profile/{profile}", "post"),
        ("/pairs/build-weekly-portfolio", "post"),
        ("/pairs/{pair_id}/manual-approve-for-review", "post"),
        ("/pairs/{pair_id}/manual-exclude", "post"),
        ("/universe/{ticker}/manual-override", "post"),
        ("/universe/{ticker}/manual-verify", "post"),
    }


def test_admin_requires_auth():
    c = TestClient(app)
    assert c.post("/admin/refresh/refresh_news").status_code == 401


def test_admin_refresh_gate_and_accept(monkeypatch):
    from app.api import admin
    from app.api.deps import get_admin_db

    db = InMemoryDB(
        {"system_control_state": [{"id": 1, "mode": "PAUSED", "provider_ingestion_enabled": False}]}
    )
    ran = []

    async def fake(db_, settings, names):
        ran.append(names)

    monkeypatch.setattr(admin, "execute_refresh", fake)
    app.dependency_overrides[get_admin_db] = lambda: db
    try:
        c = TestClient(app)
        assert c.post("/admin/refresh/refresh_news").status_code == 409  # PAUSED
        db.data["system_control_state"][0].update(
            mode="RESEARCH_ONLY", provider_ingestion_enabled=True
        )
        assert c.post("/admin/refresh/nope").status_code == 404
        r = c.post("/admin/refresh/refresh_news")
        assert r.status_code == 202 and ran == [["refresh_news"]]
        assert c.post("/admin/refresh-profile/monday").status_code == 202
    finally:
        app.dependency_overrides.clear()


def test_protected_routes_require_token():
    c = TestClient(app)
    for path in [
        "/research/status",
        "/data-quality",
        "/portfolio/manual",
        "/portfolio/score",
        "/controls/status",
    ]:
        assert c.get(path).status_code == 401


def test_dev_token_mode(monkeypatch):
    token = "x" * 32
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_env="development", dev_bearer_token=token
    )
    monkeypatch.setattr(deps, "build_service_store", lambda s: FakeStore({}))
    try:
        c = TestClient(app)
        assert (
            c.get("/controls/status", headers={"Authorization": f"Bearer {token}"}).status_code
            == 200
        )
        r = c.get("/controls/status", headers={"Authorization": "Bearer wrong"})
        assert r.status_code in (401, 503)  # falls through to JWT path, never dev store
    finally:
        app.dependency_overrides.clear()
