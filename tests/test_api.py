import os
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["SIMULATOR"] = "0"
if os.path.exists("test.db"):
    os.remove("test.db")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app, device_key  # noqa: E402


def test_flow():
    with TestClient(app) as c:
        assert c.get("/healthz").json()["status"] == "ok"
        # to'g'ri kalit bilan
        r = c.post("/api/readings", json={"device_id": "PM-01", "value": 40.5}, headers={"X-Device-Key": device_key("PM-01")})
        assert r.status_code == 201
        # noto'g'ri kalit rad etiladi
        r = c.post("/api/readings", json={"device_id": "PM-01", "value": 1}, headers={"X-Device-Key": "xato"})
        assert r.status_code == 401
        # chegaradan oshgan qiymat ogohlantirish yaratadi
        c.post("/api/readings", json={"device_id": "TMP-01", "value": 41}, headers={"X-Device-Key": device_key("TMP-01")})
        alerts = c.get("/api/alerts").json()
        assert alerts and alerts[0]["device_id"] == "TMP-01"
        latest = {d["id"]: d for d in c.get("/api/latest").json()}
        assert latest["PM-01"]["value"] == 40.5 and latest["PM-01"]["status"] == "onlayn"
        assert latest["TMP-01"]["alert"] is True
        assert len(c.get("/api/history", params={"device_id": "PM-01"}).json()) == 1
        st = c.get("/api/stats").json()
        assert st["devices_online"] == 2 and st["rejected_since_start"] == 1
        assert c.get("/").status_code == 200
