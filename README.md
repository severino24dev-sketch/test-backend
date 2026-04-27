# Truck HOS Trip Planner (Django + React)

Full-stack assessment app that accepts trip details and returns:
- Route map and stop info
- FMCSA-style daily log sheets across multiple days
- HOS-aware duty timeline using property-carrier assumptions

## Tech stack

- Backend: Django + Django REST Framework
- Frontend: React (Vite) + React Leaflet
- Routing/Geocoding APIs: OSRM + OpenStreetMap Nominatim (free)

## Assumptions implemented

- Property-carrying driver
- 70-hour / 8-day cycle
- No adverse driving conditions
- 30-minute break after 8 cumulative driving hours
- 11-hour driving limit and 14-hour duty window
- Fuel stop every 1,000 miles minimum
- 1 hour pickup and 1 hour dropoff on-duty time

## Run locally

### Backend

```bash
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py runserver
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

The frontend expects backend URL from `VITE_API_BASE` (defaults to `http://127.0.0.1:8000`).

## API

`POST /api/plan-trip/`

Request body:
```json
{
  "current_location": "Dallas, TX",
  "pickup_location": "Oklahoma City, OK",
  "dropoff_location": "Atlanta, GA",
  "current_cycle_used": 12
}
```

Response includes route geometry, stop details, trip events, and generated `daily_logs`.
