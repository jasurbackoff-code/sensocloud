# SensoCloud — bulutda IoT platformasi

Sensor ma'lumotlarini bulutda real vaqtda qabul qiluvchi, saqlovchi va ko'rsatuvchi IoT platformasi.
Toshkent shahridagi 6 ta (virtual) sensor: harorat, namlik, CO₂ va PM2.5.

Muallif: Perlipesov Jasur, 030-22 guruh. Fan: Bulutli texnologiyalar, 9-mavzu.

## Imkoniyatlar

- `POST /api/readings` — sensor o'lchovini qabul qilish (har bir qurilma o'z kaliti bilan, `X-Device-Key`)
- `GET /api/latest` — har bir sensorning oxirgi qiymati va holati (onlayn / javob bermayapti)
- `GET /api/history?device_id=PM-01&minutes=60` — sensor tarixi
- `GET /api/alerts` — chegaradan oshgan qiymatlar bo'yicha ogohlantirishlar
- `GET /api/stats` — monitoring: xabar/daqiqa, onlayn sensorlar, rad etilgan xabarlar
- `GET /healthz` — sog'liq tekshiruvi (baza ulanishi bilan)
- `/` — jonli dashboard (har 5 soniyada yangilanadi), `/docs` — API hujjati

Prototipda virtual sensorlar (simulyator) xizmat ichida ishlaydi va haqiqiy qurilma kabi
har 10 soniyada HTTPS API'ga o'lchov yuboradi. Haqiqiy qurilma (ESP32, Raspberry Pi) ham xuddi shu API'dan foydalanadi.

## Tuzilma

```
sensocloud/
├── app/
│   ├── main.py          API, ma'lumotlar bazasi, simulyator, ogohlantirishlar
│   └── static/index.html  dashboard (Chart.js)
├── tests/test_api.py    avtomatik testlar
├── Dockerfile
├── render.yaml          Render.com Blueprint (web xizmat + PostgreSQL)
└── requirements.txt
```

## Kompyuterda ishga tushirish

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000
# brauzerda: http://localhost:8000
```
yoki Docker bilan:
```bash
docker build -t sensocloud . && docker run -p 10000:10000 sensocloud
# brauzerda: http://localhost:10000
```

## Render.com'ga joylash

1. Kodni GitHub repozitoriyga yuklang.
2. render.com -> **New -> Blueprint** -> repozitoriyni tanlang -> **Apply**.
3. Bir necha daqiqada sayt `https://sensocloud-xxxx.onrender.com` manzilida ochiladi.

Eslatma: bepul xizmat 15 daqiqa ishlatilmasa uxlab qoladi (shu vaqtda simulyator ham to'xtaydi),
birinchi ochilishi ~1 daqiqa oladi. O'lchovlar 24 soat saqlanadi.

## Testlar

```bash
pip install pytest && python -m pytest -q tests
```
