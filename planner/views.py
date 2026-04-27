import math
from dataclasses import dataclass

import requests
from django.http import JsonResponse
from rest_framework.decorators import api_view
from rest_framework.response import Response


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"


@dataclass
class Segment:
    start: float
    end: float
    status: str
    label: str
    miles: float = 0.0


def geocode(address: str):
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": address, "format": "json", "limit": 1},
        headers={"User-Agent": "hos-planner-assessment"},
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not payload:
        raise ValueError(f"Could not geocode address: {address}")
    item = payload[0]
    return {
        "name": item["display_name"],
        "lat": float(item["lat"]),
        "lon": float(item["lon"]),
    }


def fetch_route(points):
    coord_str = ";".join(f"{point['lon']},{point['lat']}" for point in points)
    resp = requests.get(
        f"{OSRM_URL}/{coord_str}",
        params={"overview": "full", "geometries": "geojson", "steps": "true"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError("Unable to fetch route from OSRM.")
    return data["routes"][0]


def schedule_trip(route, current_cycle_used, location_names):
    legs = route["legs"]
    actions = []
    for i, leg in enumerate(legs):
        actions.append(
            {
                "type": "drive",
                "hours": leg["duration"] / 3600,
                "miles": leg["distance"] / 1609.34,
                "label": f"Drive: {location_names[i]} -> {location_names[i + 1]}",
            }
        )
        if i == 0:
            actions.append(
                {"type": "on_duty", "hours": 1.0, "label": f"Pickup at {location_names[1]}"}
            )
        if i == len(legs) - 1:
            actions.append(
                {"type": "on_duty", "hours": 1.0, "label": f"Dropoff at {location_names[2]}"}
            )

    timeline = []
    t = 0.0
    shift_drive = 0.0
    shift_elapsed = 0.0
    drive_since_break = 0.0
    cycle_used = float(current_cycle_used)
    miles_since_fuel = 0.0

    def add_segment(hours, status, label, miles=0.0):
        nonlocal t, shift_elapsed, cycle_used
        if hours <= 0:
            return
        timeline.append(Segment(start=t, end=t + hours, status=status, label=label, miles=miles))
        t += hours
        if status != "off_duty":
            shift_elapsed += hours
            cycle_used += hours

    def reset_shift(off_duty_hours):
        nonlocal shift_drive, shift_elapsed, drive_since_break
        add_segment(off_duty_hours, "off_duty", f"Required off-duty rest ({off_duty_hours:.0f}h)")
        shift_drive = 0.0
        shift_elapsed = 0.0
        drive_since_break = 0.0

    for action in actions:
        remaining_hours = action["hours"]
        remaining_miles = action.get("miles", 0.0)
        while remaining_hours > 1e-6:
            cycle_left = max(0.0, 70.0 - cycle_used)
            if cycle_left <= 0.0:
                add_segment(34.0, "off_duty", "34-hour restart")
                cycle_used = 0.0
                shift_drive = 0.0
                shift_elapsed = 0.0
                drive_since_break = 0.0
                continue

            if action["type"] == "on_duty":
                room = min(cycle_left, 14.0 - shift_elapsed)
                if room <= 1e-6:
                    reset_shift(10.0)
                    continue
                chunk = min(room, remaining_hours)
                add_segment(chunk, "on_duty", action["label"])
                remaining_hours -= chunk
                continue

            # Driving action
            room = min(
                cycle_left,
                14.0 - shift_elapsed,
                11.0 - shift_drive,
                8.0 - drive_since_break,
            )
            if room <= 1e-6:
                if 8.0 - drive_since_break <= 1e-6:
                    add_segment(0.5, "on_duty", "30-minute break")
                    drive_since_break = 0.0
                    continue
                reset_shift(10.0)
                continue

            avg_mph = remaining_miles / remaining_hours if remaining_hours > 0 else 50.0
            chunk = min(room, remaining_hours)
            if avg_mph > 0 and miles_since_fuel + avg_mph * chunk > 1000.0:
                fuel_hours = (1000.0 - miles_since_fuel) / avg_mph
                chunk = max(0.05, min(chunk, fuel_hours))

            chunk_miles = avg_mph * chunk
            add_segment(chunk, "driving", action["label"], chunk_miles)
            remaining_hours -= chunk
            remaining_miles = max(0.0, remaining_miles - chunk_miles)
            shift_drive += chunk
            drive_since_break += chunk
            miles_since_fuel += chunk_miles

            if miles_since_fuel >= 999.99 and remaining_hours > 1e-6:
                add_segment(0.5, "on_duty", "Fuel stop")
                miles_since_fuel = 0.0

    return timeline


def build_daily_logs(timeline):
    if not timeline:
        return []

    last_hour = max(seg.end for seg in timeline)
    total_days = int(math.ceil(last_hour / 24.0))
    days = []

    for day_index in range(total_days):
        start = day_index * 24.0
        end = start + 24.0
        day_segments = []
        totals = {"off_duty": 0.0, "sleeper": 0.0, "driving": 0.0, "on_duty": 0.0}
        remarks = []

        for seg in timeline:
            overlap_start = max(start, seg.start)
            overlap_end = min(end, seg.end)
            if overlap_end <= overlap_start:
                continue

            local_start = overlap_start - start
            local_end = overlap_end - start
            day_segments.append(
                {
                    "status": seg.status,
                    "label": seg.label,
                    "start_hour": round(local_start, 2),
                    "end_hour": round(local_end, 2),
                }
            )

            total_key = "sleeper" if seg.status == "sleeper" else seg.status
            totals[total_key] += overlap_end - overlap_start

            if abs(overlap_start - seg.start) < 1e-6:
                remarks.append({"time": round(local_start, 2), "text": seg.label})

        days.append(
            {
                "day_number": day_index + 1,
                "segments": day_segments,
                "totals": {k: round(v, 2) for k, v in totals.items()},
                "remarks": remarks[:10],
            }
        )

    return days


@api_view(["POST"])
def plan_trip(request):
    try:
        current_location = request.data["current_location"]
        pickup_location = request.data["pickup_location"]
        dropoff_location = request.data["dropoff_location"]
        current_cycle_used = float(request.data["current_cycle_used"])

        if current_cycle_used < 0 or current_cycle_used > 70:
            return JsonResponse({"error": "Current cycle used must be between 0 and 70."}, status=400)

        origin = geocode(current_location)
        pickup = geocode(pickup_location)
        dropoff = geocode(dropoff_location)
        route = fetch_route([origin, pickup, dropoff])

        timeline = schedule_trip(
            route=route,
            current_cycle_used=current_cycle_used,
            location_names=[origin["name"], pickup["name"], dropoff["name"]],
        )
        daily_logs = build_daily_logs(timeline)

        return Response(
            {
                "route": {
                    "distance_miles": round(route["distance"] / 1609.34, 2),
                    "duration_hours": round(route["duration"] / 3600, 2),
                    "coordinates": route["geometry"]["coordinates"],
                },
                "stops": [
                    {"type": "current", **origin},
                    {"type": "pickup", **pickup},
                    {"type": "dropoff", **dropoff},
                ],
                "trip_events": [
                    {
                        "status": seg.status,
                        "label": seg.label,
                        "start_hour": round(seg.start, 2),
                        "end_hour": round(seg.end, 2),
                        "miles": round(seg.miles, 2),
                    }
                    for seg in timeline
                ],
                "daily_logs": daily_logs,
                "assumptions": [
                    "Property-carrying driver",
                    "70-hour / 8-day cycle",
                    "No adverse driving conditions",
                    "Fuel stop at least every 1,000 miles",
                    "1 hour pickup and 1 hour dropoff on-duty time",
                ],
            }
        )
    except KeyError as exc:
        return JsonResponse({"error": f"Missing required field: {exc}"}, status=400)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except requests.RequestException as exc:
        return JsonResponse({"error": f"Routing service error: {exc}"}, status=502)
