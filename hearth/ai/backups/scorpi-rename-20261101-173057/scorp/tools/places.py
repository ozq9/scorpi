"""Maps and location: OpenStreetMap geocoding, places, routing (OSRM) and weather (Open-Meteo). All free, no keys."""

import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request

from . import Result, ToolError, tool

UA = {"User-Agent": "Scorp-local-assistant/1.0 (personal use)", "Accept-Language": "en"}
_nominatim_lock = threading.Lock()
_last_call = [0.0]


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _nominatim(params):
    with _nominatim_lock:  # usage policy: max 1 request per second
        wait = 1.05 - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        params = dict(params, format="jsonv2", addressdetails=1)
        return _get("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params))


def geocode_one(place):
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", str(place))
    if m:
        return {"lat": float(m.group(1)), "lon": float(m.group(2)), "name": place}
    rows = _nominatim({"q": place, "limit": 1})
    if not rows:
        raise ToolError("Could not find %r on the map." % place)
    r = rows[0]
    return {"lat": float(r["lat"]), "lon": float(r["lon"]), "name": r.get("display_name", place)}


def haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _fmt_km(km):
    return "%.0f m" % (km * 1000) if km < 1 else "%.1f km" % km


def _fmt_dur(seconds):
    minutes = int(round(seconds / 60))
    return "%d min" % minutes if minutes < 60 else "%d h %02d min" % (minutes // 60, minutes % 60)


@tool(
    "geocode",
    "Find the coordinates and full address of a place, address or landmark.",
    {"query": "string: Address or place name", "limit?": "integer: Results (default 3)"},
    group="maps",
)
def geocode(ctx, query, limit=3):
    rows = _nominatim({"q": query, "limit": min(int(limit or 3), 10)})
    if not rows:
        return Result("Nothing found for %r." % query)
    markers = [{"lat": float(r["lat"]), "lon": float(r["lon"]), "label": r.get("name") or r["display_name"].split(",")[0]} for r in rows]
    text = "\n".join("%d. %s\n   %.5f, %.5f (%s)" % (i + 1, r["display_name"], float(r["lat"]), float(r["lon"]), r.get("type", "")) for i, r in enumerate(rows))
    return Result(text, display={"type": "map", "markers": markers})


@tool(
    "find_places",
    "Find places near a location: cafes, restaurants, pharmacies, supermarkets, ATMs, hotels, parks, a specific shop name... "
    "Returns names, addresses and distances, shown on a map.",
    {"what": "string: What to look for, e.g. 'cafe', 'pharmacy', 'Lidl'", "near": "string: Address/city or 'lat,lon'", "radius_km?": "number: Search radius (default 2)", "limit?": "integer: Max results (default 10)"},
    group="maps",
)
def find_places(ctx, what, near, radius_km=2, limit=10):
    center = geocode_one(near)
    radius = max(0.2, min(float(radius_km or 2), 25))
    limit = min(int(limit or 10), 25)
    dlat = radius / 111.0
    dlon = radius / (111.0 * max(0.2, math.cos(math.radians(center["lat"]))))
    viewbox = "%f,%f,%f,%f" % (center["lon"] - dlon, center["lat"] + dlat, center["lon"] + dlon, center["lat"] - dlat)
    rows = _nominatim({"q": what, "viewbox": viewbox, "bounded": 1, "limit": limit})
    places = [{"name": r.get("name") or r["display_name"].split(",")[0], "address": r["display_name"], "lat": float(r["lat"]), "lon": float(r["lon"]), "type": r.get("type", "")} for r in rows]
    if not places:
        tag = re.sub(r"s$", "", what.strip().lower()).replace(" ", "_")
        query = (
            '[out:json][timeout:20];(nwr["amenity"="{t}"](around:{r},{la},{lo});nwr["shop"="{t}"](around:{r},{la},{lo});'
            'nwr["tourism"="{t}"](around:{r},{la},{lo});nwr["leisure"="{t}"](around:{r},{la},{lo});'
            'nwr["name"~"{n}",i](around:{r},{la},{lo}););out center {lim};'
        ).format(t=tag, n=re.escape(what.strip()), r=int(radius * 1000), la=center["lat"], lo=center["lon"], lim=limit)
        try:
            req = urllib.request.Request("https://overpass-api.de/api/interpreter", data=urllib.parse.urlencode({"data": query}).encode(), headers=UA)
            with urllib.request.urlopen(req, timeout=30) as resp:
                elements = json.loads(resp.read()).get("elements", [])
        except Exception:
            elements = []
        for el in elements:
            lat = el.get("lat") or (el.get("center") or {}).get("lat")
            lon = el.get("lon") or (el.get("center") or {}).get("lon")
            tags = el.get("tags", {})
            if lat is None or not tags.get("name"):
                continue
            street = " ".join(filter(None, [tags.get("addr:street"), tags.get("addr:housenumber")]))
            places.append({"name": tags["name"], "address": ", ".join(filter(None, [street, tags.get("addr:city")])), "lat": lat, "lon": lon,
                           "type": tags.get("amenity") or tags.get("shop") or tags.get("tourism") or "", "hours": tags.get("opening_hours", "")})
    if not places:
        return Result("No %s found within %s of %s." % (what, _fmt_km(radius), center["name"]))
    for p in places:
        p["distance"] = haversine((center["lat"], center["lon"]), (p["lat"], p["lon"]))
    places.sort(key=lambda p: p["distance"])
    places = places[:limit]
    lines = ["%d. %s — %s away%s\n   %s" % (i + 1, p["name"], _fmt_km(p["distance"]), (" · hours: " + p["hours"]) if p.get("hours") else "", p["address"]) for i, p in enumerate(places)]
    markers = [{"lat": center["lat"], "lon": center["lon"], "label": "Search center", "kind": "center"}] + [
        {"lat": p["lat"], "lon": p["lon"], "label": "%d. %s" % (i + 1, p["name"])} for i, p in enumerate(places)]
    return Result("%s near %s:\n%s" % (what, center["name"], "\n".join(lines)), display={"type": "map", "markers": markers})


@tool(
    "route",
    "Plan a route between two places by car, bike or on foot: distance, travel time, turn-by-turn steps, drawn on a map.",
    {"origin": "string: Start address or 'lat,lon'", "destination": "string: End address or 'lat,lon'", "mode?": "string[driving,cycling,walking]: Travel mode"},
    group="maps",
)
def route(ctx, origin, destination, mode="driving"):
    a = geocode_one(origin)
    b = geocode_one(destination)
    profile = {"driving": "routed-car", "cycling": "routed-bike", "walking": "routed-foot"}.get(mode or "driving", "routed-car")
    url = "https://routing.openstreetmap.de/%s/route/v1/driving/%f,%f;%f,%f?overview=full&geometries=geojson&steps=true" % (
        profile, a["lon"], a["lat"], b["lon"], b["lat"])
    try:
        data = _get(url, timeout=40)
    except Exception as exc:
        raise ToolError("Routing service unavailable: %s" % exc)
    if data.get("code") != "Ok" or not data.get("routes"):
        raise ToolError("No route found (%s)." % data.get("message", data.get("code")))
    r = data["routes"][0]
    steps = []
    for leg in r.get("legs", []):
        for st in leg.get("steps", []):
            man = st.get("maneuver", {})
            kind = man.get("type", "")
            modifier = man.get("modifier", "")
            name = st.get("name") or ""
            if kind in ("depart", "arrive") or st.get("distance", 0) > 30:
                phrase = {"depart": "Start", "arrive": "Arrive", "turn": "Turn %s" % modifier, "roundabout": "Take the roundabout",
                          "merge": "Merge", "fork": "Keep %s" % modifier, "end of road": "At the end of the road turn %s" % modifier,
                          "new name": "Continue", "continue": "Continue %s" % modifier}.get(kind, kind.capitalize())
                steps.append("%s%s (%s)" % (phrase.strip(), " onto " + name if name and kind != "arrive" else "", _fmt_km(st.get("distance", 0) / 1000)))
    coords = r["geometry"]["coordinates"]
    stride = max(1, len(coords) // 400)
    line = [[c[1], c[0]] for c in coords[::stride]] + [[coords[-1][1], coords[-1][0]]]
    summary = "%s from %s to %s: %s, about %s." % (mode.capitalize(), a["name"].split(",")[0], b["name"].split(",")[0], _fmt_km(r["distance"] / 1000), _fmt_dur(r["duration"]))
    text = summary + "\n\nSteps:\n" + "\n".join("%d. %s" % (i + 1, s) for i, s in enumerate(steps[:40]))
    display = {
        "type": "map",
        "markers": [{"lat": a["lat"], "lon": a["lon"], "label": "Start: " + a["name"].split(",")[0], "kind": "start"},
                    {"lat": b["lat"], "lon": b["lon"], "label": "End: " + b["name"].split(",")[0], "kind": "end"}],
        "line": line,
        "summary": summary,
    }
    return Result(text, display=display)


WMO = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog", 51: "light drizzle",
       53: "drizzle", 55: "dense drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers", 82: "violent showers",
       85: "snow showers", 86: "snow showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}


@tool(
    "weather",
    "Current weather and a daily forecast for any place (Open-Meteo).",
    {"place": "string: City/address or 'lat,lon'", "days?": "integer: Forecast days 1-10 (default 4)"},
    group="maps",
)
def weather(ctx, place, days=4):
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", place)
    if m:
        lat, lon, name = float(m.group(1)), float(m.group(2)), place
    else:
        geo = _get("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode({"name": place.split(",")[0], "count": 1}))
        if not geo.get("results"):
            loc = geocode_one(place)
            lat, lon, name = loc["lat"], loc["lon"], loc["name"]
        else:
            g = geo["results"][0]
            lat, lon, name = g["latitude"], g["longitude"], ", ".join(filter(None, [g.get("name"), g.get("admin1"), g.get("country")]))
    params = {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": max(1, min(int(days or 4), 10)),
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
    }
    data = _get("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params))
    cur = data.get("current", {})
    daily = data.get("daily", {})
    lines = ["Weather for %s" % name,
             "Now: %s°C (feels %s°C), %s, humidity %s%%, wind %s km/h" % (
                 cur.get("temperature_2m"), cur.get("apparent_temperature"), WMO.get(cur.get("weather_code"), "?"),
                 cur.get("relative_humidity_2m"), cur.get("wind_speed_10m"))]
    forecast = []
    for i, day in enumerate(daily.get("time", [])):
        entry = {"date": day, "max": daily["temperature_2m_max"][i], "min": daily["temperature_2m_min"][i],
                 "rain": daily["precipitation_probability_max"][i], "sky": WMO.get(daily["weather_code"][i], "?")}
        forecast.append(entry)
        lines.append("%s: %s, %s–%s°C, rain chance %s%%" % (day, entry["sky"], entry["min"], entry["max"], entry["rain"]))
    return Result("\n".join(lines), display={"type": "weather", "place": name, "now": {"temp": cur.get("temperature_2m"), "sky": WMO.get(cur.get("weather_code"), ""), "wind": cur.get("wind_speed_10m")}, "days": forecast})
