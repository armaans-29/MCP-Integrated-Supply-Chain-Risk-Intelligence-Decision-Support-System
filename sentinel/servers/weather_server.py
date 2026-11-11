"""MCP server: weather. Live via Open-Meteo (free, no key). WEATHER_MODE=demo gives deterministic offline data (flagged demo)."""
from __future__ import annotations
import hashlib
from .. import db
from ..config import env
from .common import err, http_get, make, ok

mcp = make("sentinel-weather", "weather")
DEMO = lambda: env("WEATHER_MODE", "live").lower() == "demo"
SEVERE = {95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail"}
HEAVY = {65: "heavy rain", 67: "freezing rain", 75: "heavy snow", 82: "violent rain showers", 86: "heavy snow showers"}

def _assess(days: list[dict]) -> dict:
    level, reasons = 0, []
    for d in days:
        lv, why = 0, []
        if d["wind_max_kmh"] >= 70: lv, why = max(lv, 2), why + [f"wind {d['wind_max_kmh']:.0f} km/h"]
        elif d["wind_max_kmh"] >= 50: lv, why = max(lv, 1), why + [f"wind {d['wind_max_kmh']:.0f} km/h"]
        if d["precip_mm"] >= 100: lv, why = max(lv, 2), why + [f"rain {d['precip_mm']:.0f} mm"]
        elif d["precip_mm"] >= 50: lv, why = max(lv, 1), why + [f"rain {d['precip_mm']:.0f} mm"]
        if d["weather_code"] in SEVERE: lv, why = 2, why + [SEVERE[d["weather_code"]]]
        elif d["weather_code"] in HEAVY: lv, why = max(lv, 1), why + [HEAVY[d["weather_code"]]]
        if why:
            reasons.append(f"{d['date']}: {', '.join(why)}")
        level = max(level, lv)
    return {"level": ["Low", "Medium", "High"][level], "reasons": reasons}

def _demo_days(lat, lon, n):
    h = int(hashlib.md5(f"{round(lat,1)}{round(lon,1)}".encode()).hexdigest(), 16)
    from datetime import date, timedelta
    out = []
    for i in range(n):
        x = (h >> (i * 5)) % 100
        out.append({"date": str(date.today() + timedelta(days=i)), "weather_code": 95 if x > 92 else 65 if x > 80 else 2, "precip_mm": x * 1.1 if x > 70 else x / 10,
                    "wind_max_kmh": 30 + x * .6, "temp_max_c": 28 + (x % 9)})
    return out

@mcp.tool()
def weather_status() -> dict:
    """Health check for the weather provider."""
    if DEMO():
        db.set_integration("weather", "demo", "demo", "WEATHER_MODE=demo")
        return ok({"detail": "Demo mode – deterministic offline data."}, "demo", "demo adapter")
    good, d = http_get("https://api.open-meteo.com/v1/forecast", {"latitude": 0, "longitude": 0, "daily": "weather_code", "forecast_days": 1}, timeout=6, retries=0)
    db.set_integration("weather", "live", "connected" if good else "error", "Open-Meteo reachable" if good else str(d))
    return ok({"detail": "Open-Meteo reachable"}, "live", "Open-Meteo") if good else err(f"Open-Meteo unreachable: {d}", "live")

@mcp.tool()
def get_weather_conditions(location: str = "", latitude: float = 0.0, longitude: float = 0.0, days: int = 5) -> dict:
    """Daily forecast and disruption risk (Low/Medium/High) for a location name or lat/lon – use a shipment's current position to assess route risk."""
    days = min(max(days, 1), 10)
    name = location
    if not (latitude or longitude):
        if not location:
            return err("Provide a location name or latitude/longitude.", "live")
        if DEMO():
            latitude, longitude = 20.0, 78.0
        else:
            good, g = http_get("https://geocoding-api.open-meteo.com/v1/search", {"name": location.split("(")[0].strip(), "count": 1}, timeout=8)
            if not good or not g.get("results"):
                return err(f"Could not geocode '{location}'." if good else f"Geocoding failed: {g}", "live")
            latitude, longitude, name = g["results"][0]["latitude"], g["results"][0]["longitude"], g["results"][0]["name"]
    if DEMO():
        d = _demo_days(latitude, longitude, days)
        return ok({"location": name or f"{latitude},{longitude}", "forecast": d, "risk": _assess(d)}, "demo", "demo adapter (synthetic – not a real forecast)")
    good, r = http_get("https://api.open-meteo.com/v1/forecast", {"latitude": latitude, "longitude": longitude, "forecast_days": days, "timezone": "auto",
                       "daily": "weather_code,precipitation_sum,wind_speed_10m_max,temperature_2m_max"}, timeout=10)
    if not good:
        return err(f"Weather service error: {r}", "live")
    dl = r["daily"]
    fc = [{"date": dl["time"][i], "weather_code": dl["weather_code"][i] or 0, "precip_mm": dl["precipitation_sum"][i] or 0.0,
           "wind_max_kmh": dl["wind_speed_10m_max"][i] or 0.0, "temp_max_c": dl["temperature_2m_max"][i]} for i in range(len(dl["time"]))]
    return ok({"location": name or f"{latitude:.2f},{longitude:.2f}", "forecast": fc, "risk": _assess(fc)}, "live", "Open-Meteo")

if __name__ == "__main__":
    db.init_db()
    mcp.run(transport="streamable-http")
