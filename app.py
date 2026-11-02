# -*- coding: utf-8 -*-
"""Aplicación web de Mareas Las Grutas. Basada en la lógica del programa de escritorio."""
import math
import re
from datetime import date, datetime, time, timedelta
from functools import wraps
from threading import Lock
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

SOURCE_URLS = [
    "https://lasgrutasdepartamentos.com/mareas-las-grutas",
    "https://rionegrino.com/mareas/las-grutas/",
]
WIND_URL = "https://api.open-meteo.com/v1/forecast"
LAS_GRUTAS_LAT, LAS_GRUTAS_LON = -40.81, -65.09
WIND_TZ = "America/Argentina/Buenos_Aires"
LOW_LIMIT, HIGH_LIMIT = 0.25, 0.75
NEAR_EXTREME_MIN, MIN_GAP_HOURS, MAX_GAP_HOURS = 45, 3.0, 9.0
MIN_RANGE_M, GRAPH_HOURS, WIND_MAX_GAP_MIN = 0.05, 9, 90

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
    "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}
MONTHS_SHORT = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}
SKY_BY_CODE = {
    2: "Parcialmente nublado", 3: "Nublado", 45: "Niebla", 48: "Niebla",
    51: "Llovizna", 53: "Llovizna", 55: "Llovizna", 56: "Llovizna helada",
    57: "Llovizna helada", 61: "Lluvia", 63: "Lluvia", 65: "Lluvia",
    66: "Lluvia helada", 67: "Lluvia helada", 71: "Nieve", 73: "Nieve",
    75: "Nieve", 77: "Nieve", 80: "Chubascos", 81: "Chubascos",
    82: "Chubascos", 85: "Chubascos de nieve", 86: "Chubascos de nieve",
    95: "Tormenta", 96: "Tormenta", 99: "Tormenta",
}
COMPASS8 = [
    ("N", "Norte"), ("NE", "Noreste"), ("E", "Este"), ("SE", "Sudeste"),
    ("S", "Sur"), ("SO", "Sudoeste"), ("O", "Oeste"), ("NO", "Noroeste"),
]
cache = {"at": None, "events": None, "source": None, "forecast": None, "forecast_error": None}
cache_lock = Lock()
CACHE_SECONDS = 15 * 60


def parse_date_from_text(text, current_year=None):
    current_year = current_year or date.today().year
    numeric = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", text)
    if numeric:
        d, m, y = numeric.groups()
        year = int(y) if y else current_year
        if year < 100:
            year += 2000
        try:
            return date(year, int(m), int(d))
        except ValueError:
            return None
    lowered = text.lower()
    for month_name, month_num in MONTHS.items():
        match = re.search(rf"\b(\d{{1,2}})\s+{month_name}\b", lowered)
        if match:
            try:
                return date(current_year, month_num, int(match.group(1)))
            except ValueError:
                return None
    for month_name, month_num in MONTHS_SHORT.items():
        match = re.search(rf"\b(\d{{1,2}})\s+{month_name}\.?\b", lowered)
        if match:
            try:
                return date(current_year, month_num, int(match.group(1)))
            except ValueError:
                return None
    return None


def parse_height(text):
    match = re.search(r"(?<!\d)(\d{1,2}(?:[.,]\d{1,2})?)\s*m\b", text.lower())
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def parse_time(text):
    match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    return time(int(match.group(1)), int(match.group(2))) if match else None


def parse_tide_events(html):
    soup = BeautifulSoup(html, "html.parser")
    events, current_date = [], None
    current_year = date.today().year
    for row in soup.find_all(["tr", "li", "p"]):
        text = " ".join(row.stripped_strings)
        if not text:
            continue
        found_date = parse_date_from_text(text, current_year)
        if found_date:
            current_date = found_date
        lower = text.lower()
        if not any(w in lower for w in ("pleamar", "bajamar", "marea alta", "marea baja")):
            continue
        tide_type = "Pleamar" if ("pleamar" in lower or "marea alta" in lower) else "Bajamar"
        event_time, height = parse_time(text), parse_height(text)
        if event_time is None or height is None:
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            if cells:
                joined = " ".join(cells)
                event_time = event_time or parse_time(joined)
                height = height if height is not None else parse_height(joined)
                lower += " " + joined.lower()
                if "pleamar" in lower or "marea alta" in lower:
                    tide_type = "Pleamar"
                elif "bajamar" in lower or "marea baja" in lower:
                    tide_type = "Bajamar"
        if event_time and height is not None and current_date:
            events.append({"datetime": datetime.combine(current_date, event_time),
                           "height": height, "type": tide_type})
    if len(events) < 4:
        lines = [x.strip() for x in soup.get_text("\n", strip=True).splitlines() if x.strip()]
        active_date = None
        for line in lines:
            found_date = parse_date_from_text(line, current_year)
            if found_date:
                active_date = found_date
            lower = line.lower()
            if not any(w in lower for w in ("pleamar", "bajamar", "marea alta", "marea baja")):
                continue
            tm, ht = parse_time(line), parse_height(line)
            if tm and ht is not None and active_date:
                typ = "Pleamar" if ("pleamar" in lower or "marea alta" in lower) else "Bajamar"
                events.append({"datetime": datetime.combine(active_date, tm), "height": ht, "type": typ})
    unique = {}
    for event in events:
        key = (event["datetime"], event["type"], round(event["height"], 2))
        unique[key] = event
    return sorted(unique.values(), key=lambda x: x["datetime"])


def fetch_events():
    errors = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for url in SOURCE_URLS:
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            response.encoding = response.apparent_encoding
            events = [e for e in parse_tide_events(response.text) if -1 <= e["height"] <= 15]
            if len(events) >= 4:
                return events, url
            errors.append(f"{url}: se encontraron solo {len(events)} eventos")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("No se pudieron obtener suficientes datos de mareas. " + " | ".join(errors))


def classify_level(pct):
    if pct is None:
        return None
    if pct < LOW_LIMIT:
        return "baja"
    if pct < HIGH_LIMIT:
        return "intermedia"
    return "alta"


def estimate_tide(events, target):
    events = sorted(events, key=lambda x: x["datetime"])
    before, after = None, None
    for event in events:
        if event["datetime"] <= target:
            before = event
        elif event["datetime"] > target:
            after = event
            break
    if before is None or after is None:
        return None
    total = (after["datetime"] - before["datetime"]).total_seconds()
    elapsed = (target - before["datetime"]).total_seconds()
    if total <= 0:
        return None
    fraction = max(0.0, min(1.0, elapsed / total))
    blend = (1 - math.cos(math.pi * fraction)) / 2
    height = before["height"] + (after["height"] - before["height"]) * blend
    trend = "Subiendo" if after["height"] > before["height"] else "Bajando" if after["height"] < before["height"] else "Estable"
    low_h, high_h = min(before["height"], after["height"]), max(before["height"], after["height"])
    span = high_h - low_h
    pct = max(0.0, min(1.0, (height - low_h) / span)) if span >= MIN_RANGE_M else None
    nearest = min((before, after), key=lambda ev: abs((ev["datetime"] - target).total_seconds()))
    mins_nearest = abs((nearest["datetime"] - target).total_seconds()) / 60
    phase = nearest["type"] if mins_nearest <= NEAR_EXTREME_MIN else "Entre extremos"
    warning = None
    gap_hours = total / 3600
    if before["type"] == after["type"]:
        warning = "Los datos de la fuente no alternan pleamar y bajamar en este tramo; la estimación puede no ser confiable."
    elif gap_hours > MAX_GAP_HOURS or gap_hours < MIN_GAP_HOURS:
        warning = "La separación entre los horarios publicados para este tramo es poco habitual; puede faltar algún dato y la estimación podría no ser confiable."
    next_high = next((e for e in events if e["datetime"] > target and e["type"] == "Pleamar"), None)
    next_low = next((e for e in events if e["datetime"] > target and e["type"] == "Bajamar"), None)
    return {"height": height, "trend": trend, "phase": phase, "before": before, "after": after,
            "next_high": next_high, "next_low": next_low, "pct": pct,
            "level": classify_level(pct), "warning": warning}


def fetch_forecast():
    params = {
        "latitude": LAS_GRUTAS_LAT, "longitude": LAS_GRUTAS_LON,
        "hourly": "wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,apparent_temperature,cloud_cover,weather_code,is_day",
        "wind_speed_unit": "kmh", "timezone": WIND_TZ, "past_days": 1, "forecast_days": 7,
    }
    response = requests.get(WIND_URL, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    hourly = data["hourly"]
    keys = ["time", "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m"]
    if not all(k in hourly for k in keys):
        raise RuntimeError("La respuesta del pronóstico está incompleta.")
    rows = []
    optional_keys = ["temperature_2m", "apparent_temperature", "cloud_cover", "weather_code", "is_day"]
    for i, stamp in enumerate(hourly["time"]):
        def get(k):
            values = hourly.get(k)
            return values[i] if isinstance(values, list) and i < len(values) else None
        try:
            rows.append({
                "datetime": datetime.fromisoformat(stamp),
                "speed": float(get("wind_speed_10m")),
                "gust": float(get("wind_gusts_10m")) if get("wind_gusts_10m") is not None else None,
                "direction": float(get("wind_direction_10m")),
                "temp": float(get("temperature_2m")) if get("temperature_2m") is not None else None,
                "feels": float(get("apparent_temperature")) if get("apparent_temperature") is not None else None,
                "cloud": float(get("cloud_cover")) if get("cloud_cover") is not None else None,
                "code": int(get("weather_code")) if get("weather_code") is not None else None,
                "is_day": bool(get("is_day")) if get("is_day") is not None else None,
            })
        except (TypeError, ValueError):
            continue
    return rows


def get_data():
    now = datetime.now()
    with cache_lock:
        fresh = cache["at"] and (now - cache["at"]).total_seconds() < CACHE_SECONDS
        if fresh and cache["events"] is not None:
            return cache
    events, source = fetch_events()
    try:
        forecast = fetch_forecast()
        forecast_error = None
    except Exception as exc:
        forecast, forecast_error = [], str(exc)
    with cache_lock:
        cache.update(at=now, events=events, source=source, forecast=forecast, forecast_error=forecast_error)
        return cache


def compass_label(degrees):
    index = int((degrees % 360) / 45 + 0.5) % 8
    abbr, name = COMPASS8[index]
    return f"{name} ({abbr})"


def sky_text(code, cloud, is_day):
    day = True if is_day is None else is_day
    if code in (0, 1):
        return ("Soleado" if day else "Despejado") if code == 0 else ("Mayormente soleado" if day else "Mayormente despejado")
    if code in SKY_BY_CODE:
        return SKY_BY_CODE[code]
    if cloud is not None:
        return "Soleado" if cloud < 20 and day else "Despejado" if cloud < 20 else "Parcialmente nublado" if cloud < 50 else "Nublado"
    return "Sin descripción"


def event_json(event, target):
    if not event:
        return None
    dt = event["datetime"]
    minutes = max(0, int(round((dt - target).total_seconds() / 60)))
    hours, mins = divmod(minutes, 60)
    countdown = f"{hours} h {mins:02d} min" if hours else f"{mins} min"
    return {"time": dt.strftime("%H:%M"), "date": dt.strftime("%d/%m/%Y"),
            "height": event["height"], "type": event["type"], "countdown": countdown}


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/data")
def api_data():
    try:
        data = get_data()
        raw_date = request.args.get("date", date.today().isoformat())
        raw_time = request.args.get("time", datetime.now().strftime("%H:%M"))
        target = datetime.strptime(f"{raw_date} {raw_time}", "%Y-%m-%d %H:%M")
        events = data["events"]
        est = estimate_tide(events, target)
        if est is None:
            return jsonify({"ok": False, "error": "No hay horarios suficientes para estimar la marea en esa fecha y hora.",
                            "available_from": events[0]["datetime"].strftime("%d/%m/%Y %H:%M"),
                            "available_to": events[-1]["datetime"].strftime("%d/%m/%Y %H:%M")}), 422
        forecast_reading = None
        if data["forecast"]:
            closest = min(data["forecast"], key=lambda x: abs((x["datetime"] - target).total_seconds()))
            if abs((closest["datetime"] - target).total_seconds()) <= WIND_MAX_GAP_MIN * 60:
                forecast_reading = {
                    "wind": round(closest["speed"]), "gust": round(closest["gust"]) if closest["gust"] is not None else None,
                    "direction": compass_label(closest["direction"]), "temp": round(closest["temp"]) if closest["temp"] is not None else None,
                    "feels": round(closest["feels"]) if closest["feels"] is not None else None,
                    "sky": sky_text(closest["code"], closest["cloud"], closest["is_day"]),
                    "cloud": round(closest["cloud"]) if closest["cloud"] is not None else None,
                    "forecast_time": closest["datetime"].strftime("%H:%M"),
                }
        start, end = target - timedelta(hours=GRAPH_HOURS), target + timedelta(hours=GRAPH_HOURS)
        graph = []
        cursor = start
        while cursor <= end:
            point = estimate_tide(events, cursor)
            if point:
                graph.append({"time": cursor.strftime("%H:%M"), "datetime": cursor.isoformat(), "height": round(point["height"], 3)})
            cursor += timedelta(minutes=10)
        nearby_events = [{"datetime": e["datetime"].isoformat(), "time": e["datetime"].strftime("%H:%M"),
                          "height": e["height"], "type": e["type"]}
                         for e in events if start <= e["datetime"] <= end]
        return jsonify({
            "ok": True, "target": target.strftime("%A %d/%m/%Y · %H:%M"),
            "height": round(est["height"], 2), "trend": est["trend"], "phase": est["phase"],
            "level": est["level"], "percentage": round(est["pct"] * 100) if est["pct"] is not None else None,
            "warning": est["warning"], "next_high": event_json(est["next_high"], target),
            "next_low": event_json(est["next_low"], target), "forecast": forecast_reading,
            "forecast_error": data["forecast_error"], "graph": graph, "events": nearby_events,
            "source": urlparse(data["source"]).netloc, "updated": data["at"].strftime("%H:%M"),
            "available_from": events[0]["datetime"].strftime("%d/%m/%Y"),
            "available_to": events[-1]["datetime"].strftime("%d/%m/%Y"),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": f"No se pudieron cargar los datos: {exc}"}), 502


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True)
