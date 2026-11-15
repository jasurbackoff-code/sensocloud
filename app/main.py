"""SensoCloud: sensor ma'lumotlarini bulutda real vaqtda qabul qiluvchi IoT platformasi.

API:
  POST /api/readings            sensor o'lchovini qabul qilish (X-Device-Key sarlavhasi bilan)
  GET  /api/devices             sensorlar ro'yxati va holati
  GET  /api/latest              har bir sensorning oxirgi qiymati
  GET  /api/history             bitta sensor tarixi (?device_id=PM-01&minutes=60)
  GET  /api/alerts              oxirgi ogohlantirishlar
  GET  /api/stats               monitoring: xabarlar soni, onlayn sensorlar, xatolar
  GET  /healthz                 sog'liq tekshiruvi
"""
import asyncio
import hashlib
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import (Column, DateTime, Float, ForeignKey, Integer, String, create_engine, delete, func,
                        select)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sensocloud")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./sensocloud.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
DEVICE_SECRET = os.getenv("DEVICE_SECRET", "dev-secret")
SIMULATOR = os.getenv("SIMULATOR", "1") == "1"
SIM_INTERVAL = float(os.getenv("SIM_INTERVAL", "10"))
PORT = int(os.getenv("PORT", "8000"))
RETENTION_HOURS = int(os.getenv("RETENTION_HOURS", "24"))
OFFLINE_AFTER = 60  # soniya: shundan ko'p xabar kelmasa sensor "javob bermayapti"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
STATIC = Path(__file__).parent / "static"
STARTED = time.time()
COUNTERS = {"accepted": 0, "rejected": 0}


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "devices"
    id = Column(String(20), primary_key=True)
    type = Column(String(30), nullable=False)
    location = Column(String(60), nullable=False)
    unit = Column(String(10), nullable=False)
    min_limit = Column(Float)
    max_limit = Column(Float)


class Reading(Base):
    __tablename__ = "readings"
    id = Column(Integer, primary_key=True)
    device_id = Column(String(20), ForeignKey("devices.id"), index=True, nullable=False)
    value = Column(Float, nullable=False)
    ts = Column(DateTime(timezone=True), index=True, nullable=False)


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True)
    device_id = Column(String(20), ForeignKey("devices.id"), index=True, nullable=False)
    value = Column(Float, nullable=False)
    message = Column(String(200), nullable=False)
    ts = Column(DateTime(timezone=True), index=True, nullable=False)


# Prototipdagi 6 ta virtual sensor: (id, turi, joylashuv, birlik, min, max, simulyator: o'rtacha, tebranish)
DEVICES = [
    ("TMP-01", "Harorat", "Chilonzor", "°C", None, 38, 24, 6),
    ("HUM-01", "Namlik", "Yunusobod", "%", 20, 85, 45, 15),
    ("CO2-01", "CO₂", "Sergeli", "ppm", None, 1000, 650, 220),
    ("CO2-02", "CO₂", "Mirzo Ulug'bek", "ppm", None, 1000, 600, 200),
    ("PM-01", "PM2.5", "Olmazor", "µg/m³", None, 55, 35, 15),
    ("PM-02", "PM2.5", "Yakkasaroy", "µg/m³", None, 55, 30, 14),
]


def device_key(device_id: str) -> str:
    """Har bir qurilma uchun alohida kalit (DEVICE_SECRET asosida hosil qilinadi)."""
    return hashlib.sha256(f"{DEVICE_SECRET}:{device_id}".encode()).hexdigest()[:24]


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def init_db():
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        for d in DEVICES:
            if not s.get(Device, d[0]):
                s.add(Device(id=d[0], type=d[1], location=d[2], unit=d[3], min_limit=d[4], max_limit=d[5]))
        s.commit()


def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ---------------- Virtual sensorlar (simulyator) ----------------
async def simulator():
    """Har bir sensor haqiqiy qurilma kabi HTTPS API'ga (bu yerda o'z manzilimizga) o'lchov yuboradi."""
    await asyncio.sleep(3)
    url = f"http://127.0.0.1:{PORT}/api/readings"
    state = {d[0]: d[6] for d in DEVICES}
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            hour = datetime.now().hour
            for d in DEVICES:
                did, mean, spread = d[0], d[6], d[7]
                daily = spread * 0.5 if 12 <= hour <= 18 else -spread * 0.2  # kunduzi qiymatlar yuqoriroq
                target = mean + daily + random.gauss(0, spread * 0.35)
                if random.random() < 0.03:                                    # ba'zida keskin sakrash
                    target += spread * 2.2
                state[did] = round(state[did] * 0.6 + target * 0.4, 1)
                try:
                    await client.post(url, json={"device_id": did, "value": state[did]},
                                      headers={"X-Device-Key": device_key(did)})
                except httpx.HTTPError as e:
                    log.warning("simulyator: %s yuborilmadi: %s", did, e)
            await asyncio.sleep(SIM_INTERVAL)


async def cleanup():
    """Eski o'lchovlarni o'chiradi (bepul bazada joy tejash uchun)."""
    while True:
        await asyncio.sleep(600)
        with SessionLocal() as s:
            cutoff = now() - timedelta(hours=RETENTION_HOURS)
            s.execute(delete(Reading).where(Reading.ts < cutoff))
            s.execute(delete(Alert).where(Alert.ts < cutoff))
            s.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    tasks = [asyncio.create_task(cleanup())]
    if SIMULATOR:
        tasks.append(asyncio.create_task(simulator()))
        log.info("Simulyator yoqildi: %d sensor, har %.0f soniyada", len(DEVICES), SIM_INTERVAL)
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="SensoCloud IoT API", version="1.0.0", lifespan=lifespan)


# ---------------- API ----------------
class ReadingIn(BaseModel):
    device_id: str = Field(..., max_length=20)
    value: float = Field(..., ge=-1000, le=100000)
    ts: Optional[datetime] = None


def check_alert(s: Session, dev: Device, value: float, ts: datetime):
    msg = None
    if dev.max_limit is not None and value > dev.max_limit:
        msg = f"{dev.type} chegaradan yuqori: {value} {dev.unit} (chegara {dev.max_limit:g})"
    elif dev.min_limit is not None and value < dev.min_limit:
        msg = f"{dev.type} chegaradan past: {value} {dev.unit} (chegara {dev.min_limit:g})"
    if msg:
        s.add(Alert(device_id=dev.id, value=value, message=msg, ts=ts))
        log.warning("OGOHLANTIRISH %s %s: %s", dev.id, dev.location, msg)


@app.post("/api/readings", status_code=201)
def post_reading(body: ReadingIn, x_device_key: str = Header(default=""), s: Session = Depends(db)):
    dev = s.get(Device, body.device_id)
    if not dev or x_device_key != device_key(body.device_id):
        COUNTERS["rejected"] += 1
        raise HTTPException(401, "Qurilma yoki kalit noto'g'ri")
    ts = aware(body.ts) if body.ts else now()
    s.add(Reading(device_id=dev.id, value=body.value, ts=ts))
    check_alert(s, dev, body.value, ts)
    s.commit()
    COUNTERS["accepted"] += 1
    return {"status": "ok", "device_id": dev.id, "value": body.value, "ts": ts.isoformat()}


def latest_map(s: Session) -> dict:
    sub = select(Reading.device_id, func.max(Reading.id).label("mid")).group_by(Reading.device_id).subquery()
    rows = s.execute(select(Reading).join(sub, Reading.id == sub.c.mid)).scalars().all()
    return {r.device_id: r for r in rows}


def device_status(r: Optional[Reading]) -> str:
    if not r:
        return "ma'lumot yo'q"
    return "onlayn" if (now() - aware(r.ts)).total_seconds() <= OFFLINE_AFTER else "javob bermayapti"


@app.get("/api/devices")
def devices(s: Session = Depends(db)):
    last = latest_map(s)
    return [{"id": d.id, "type": d.type, "location": d.location, "unit": d.unit,
             "min_limit": d.min_limit, "max_limit": d.max_limit, "status": device_status(last.get(d.id))}
            for d in s.scalars(select(Device).order_by(Device.id))]


@app.get("/api/latest")
def latest(s: Session = Depends(db)):
    last = latest_map(s)
    out = []
    for d in s.scalars(select(Device).order_by(Device.id)):
        r = last.get(d.id)
        over = r is not None and ((d.max_limit is not None and r.value > d.max_limit) or
                                  (d.min_limit is not None and r.value < d.min_limit))
        out.append({"id": d.id, "type": d.type, "location": d.location, "unit": d.unit,
                    "value": r.value if r else None, "ts": aware(r.ts).isoformat() if r else None,
                    "status": device_status(r), "alert": over})
    return out


@app.get("/api/history")
def history(device_id: str, minutes: int = Query(60, ge=1, le=1440), s: Session = Depends(db)):
    if not s.get(Device, device_id):
        raise HTTPException(404, "Sensor topilmadi")
    since = now() - timedelta(minutes=minutes)
    rows = s.scalars(select(Reading).where(Reading.device_id == device_id, Reading.ts >= since)
                     .order_by(Reading.ts)).all()
    step = max(1, len(rows) // 300)  # grafik uchun ko'pi bilan ~300 nuqta
    return [{"ts": aware(r.ts).isoformat(), "value": r.value} for r in rows[::step]]


@app.get("/api/alerts")
def alerts(limit: int = Query(20, ge=1, le=200), s: Session = Depends(db)):
    rows = s.execute(select(Alert, Device).join(Device, Alert.device_id == Device.id)
                     .order_by(Alert.id.desc()).limit(limit)).all()
    return [{"device_id": a.device_id, "location": d.location, "value": a.value, "message": a.message,
             "ts": aware(a.ts).isoformat()} for a, d in rows]


@app.get("/api/stats")
def stats(s: Session = Depends(db)):
    t = now()
    last_min = s.scalar(select(func.count(Reading.id)).where(Reading.ts >= t - timedelta(minutes=1)))
    last_hour = s.scalar(select(func.count(Reading.id)).where(Reading.ts >= t - timedelta(hours=1)))
    total = s.scalar(select(func.count(Reading.id)))
    alerts_hour = s.scalar(select(func.count(Alert.id)).where(Alert.ts >= t - timedelta(hours=1)))
    last = latest_map(s)
    online = sum(1 for d in DEVICES if device_status(last.get(d[0])) == "onlayn")
    return {"devices_total": len(DEVICES), "devices_online": online,
            "messages_last_minute": last_min, "messages_last_hour": last_hour, "readings_stored": total,
            "alerts_last_hour": alerts_hour, "accepted_since_start": COUNTERS["accepted"],
            "rejected_since_start": COUNTERS["rejected"], "uptime_seconds": int(time.time() - STARTED),
            "simulator": SIMULATOR}


@app.get("/healthz")
def healthz(s: Session = Depends(db)):
    s.execute(select(1))
    return {"status": "ok", "database": "ok"}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
