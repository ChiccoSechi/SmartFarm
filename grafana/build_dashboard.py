#!/usr/bin/env python3
"""Build smartfarm.json from a declarative spec (single source of truth for layout and
Flux queries); the from/range/... format lets gen_zone_dashboards.py inject the zone filter."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DASH_DIR = os.path.join(HERE, "provisioning", "dashboards")
OUT = os.path.join(DASH_DIR, "smartfarm.json")

# ACTUATORS from the simulator: nominal powers are imported, not copied.
sys.path.insert(0, os.path.dirname(HERE))
from sensor_simulator import ACTUATORS

# DS and NAV_LINK shared with the zone/map generators (grafana/_common.py).
from _common import DS, NAV_LINK

SEP = "\n  |> "
RANGE_TR = "range(start: v.timeRangeStart, stop: v.timeRangeStop)"

# 'suffix: W' appends ' W' with no SI autoscaling: raw watts, matching the Flux overload threshold.
UNIT_WATT = "suffix: W"

# Threshold lines: must match the Flux tasks in influxdb/tasks/*.flux.
THRESHOLD_PM25 = 50    # alert_pm25.flux: r._value > 50.0
THRESHOLD_SOIL = 20    # alert_soil_moisture.flux: r._value < 20.0

# Red threshold at 1.5x each actuator's nominal power (same as the Flux task).
OVERLOAD_FACTOR = 1.5
MONITORED_NOMINALS = {a["id"]: float(a["nominal_w"]) for a in ACTUATORS}

# panel id counter
_next_id = 0
def new_id():
    global _next_id
    _next_id += 1
    return _next_id


def flux(bucket, measurement, fields, keep_cols, *, range_expr=RANGE_TR,
         agg=True, last_by=None):
    """Build a Flux query in the format expected by gen_zone_dashboards.py."""
    if len(fields) == 1:
        cond = f'r._measurement == "{measurement}" and r._field == "{fields[0]}"'
    elif fields:
        ors = " or ".join(f'r._field == "{f}"' for f in fields)
        cond = f'r._measurement == "{measurement}" and ({ors})'
    else:
        cond = f'r._measurement == "{measurement}"'
    parts = [f'from(bucket: "{bucket}")', range_expr,
             f"filter(fn: (r) => {cond})"]
    if last_by is not None:
        cols = ", ".join(f'"{c}"' for c in last_by)
        parts += [f"group(columns: [{cols}])", "last()"]
    elif agg:
        parts.append("aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)")
    keep = ", ".join(f'"{c}"' for c in keep_cols)
    parts.append(f"keep(columns: [{keep}])")
    return SEP.join(parts)


def thresholds(steps):
    return {"mode": "absolute",
            "steps": [{"color": c, "value": v} for c, v in steps]}


def timeseries(title, query, *, unit="", decimals=None, threshold=None,
               description=""):
    """Timeseries panel. threshold=(value, color) draws a line."""
    steps = [{"color": "green", "value": None}]
    tstyle = "off"
    if threshold is not None:
        val, col = threshold
        steps = [{"color": "green", "value": None}, {"color": col, "value": val}]
        tstyle = "line"
    defaults = {
        "color": {"mode": "palette-classic"},
        "custom": {
            "axisBorderShow": False, "axisCenteredZero": False,
            "axisColorMode": "text", "axisLabel": "", "axisPlacement": "auto",
            "barAlignment": 0, "drawStyle": "line", "fillOpacity": 8,
            "gradientMode": "none",
            "hideFrom": {"legend": False, "tooltip": False, "viz": False},
            "insertNulls": False, "lineInterpolation": "linear", "lineWidth": 1,
            "pointSize": 5, "scaleDistribution": {"type": "linear"},
            "showPoints": "auto", "spanNulls": False,
            "stacking": {"group": "A", "mode": "none"},
            "thresholdsStyle": {"mode": tstyle},
        },
        "mappings": [], "thresholds": thresholds([(s["color"], s["value"]) for s in steps]),
    }
    if unit:
        defaults["unit"] = unit
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "datasource": DS, "description": description,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "id": new_id(),
        "options": {
            "legend": {"calcs": [], "displayMode": "list", "placement": "bottom",
                       "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "none"},
        },
        "targets": [{"datasource": DS, "query": query, "refId": "A"}],
        "title": title, "type": "timeseries",
    }


def stat(title, query, *, unit="", decimals=None, description="",
         overrides=None):
    """Stat panel with no default red threshold: the alarm color must be a real condition,
    passed per series via `overrides`."""
    defaults = {
        "color": {"mode": "thresholds"}, "mappings": [],
        "thresholds": thresholds([("green", None)]),
    }
    if unit:
        defaults["unit"] = unit
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "datasource": DS, "description": description,
        "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
        "id": new_id(),
        "options": {
            "colorMode": "value", "graphMode": "area", "justifyMode": "auto",
            "orientation": "auto", "reduceOptions": {
                "calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "value_and_name", "wideLayout": True,
        },
        "pluginVersion": "11.2.0",
        "targets": [{"datasource": DS, "query": query, "refId": "A"}],
        "title": title, "type": "stat",
    }


def table(title, query, *, description=""):
    """Table panel for alert events: Grafana only displays them; events are already decided
    by the Flux tasks (bucket "alerts")."""
    return {
        "datasource": DS, "description": description,
        "fieldConfig": {"defaults": {
            "color": {"mode": "thresholds"},
            "custom": {"align": "auto", "cellOptions": {"type": "auto"},
                       "inspect": False},
            "mappings": [], "thresholds": thresholds([("green", None)]),
        }, "overrides": []},
        "id": new_id(),
        "options": {"cellHeight": "sm", "footer": {
            "countRows": False, "fields": "", "reducer": ["sum"],
            "show": False}, "showHeader": True},
        "pluginVersion": "11.2.0",
        "targets": [{"datasource": DS, "query": query, "refId": "A"}],
        "title": title, "type": "table",
    }


def alert_count_stat(title, alert_type, description=""):
    """Stat counting the events of an alert_type in the period (bucket "alerts"):
    0=green, >=1=red. noValue="0" avoids "No data"; textMode="value" (number only) because
    value_and_name showed the leftover _start/_stop columns of the Flux response as the name."""
    query = SEP.join([
        'from(bucket: "alerts")', RANGE_TR,
        f'filter(fn: (r) => r._measurement == "alert_events" and r.alert_type == "{alert_type}")',
        "group()",
        'count(column: "_value")',
    ])
    return {
        "datasource": DS, "description": description,
        "fieldConfig": {"defaults": {
            "color": {"mode": "thresholds"}, "mappings": [],
            "noValue": "0",
            "thresholds": thresholds([("green", None), ("red", 1)]),
        }, "overrides": []},
        "id": new_id(),
        "options": {
            "colorMode": "background", "graphMode": "none",
            "justifyMode": "auto", "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "",
                              "values": False},
            "textMode": "value", "wideLayout": True,
        },
        "pluginVersion": "11.2.0",
        "targets": [{"datasource": DS, "query": query, "refId": "A"}],
        "title": title, "type": "stat",
    }


def actuator_overrides():
    """Overrides for the "Actuator status" panel: red when the value exceeds 1.5x that
    actuator's nominal power (like the overload alert); one override per actuator, matched
    via byRegexp on actuator_id."""
    return [{
        "matcher": {"id": "byRegexp", "options": f".*{aid}.*"},
        "properties": [{"id": "thresholds", "value": thresholds(
            [("green", None), ("red", OVERLOAD_FACTOR * nominal)])}],
    } for aid, nominal in sorted(MONITORED_NOMINALS.items())]


def row(title):
    return {"type": "row", "id": new_id(), "title": title, "collapsed": False,
            "panels": [], "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0}}


# Spec: rows (per sensor type) and panels.
K = ["_time", "_value", "zone"]          # base keep: 1 field, one series per zone
KF = ["_time", "_value", "zone", "_field"]  # multi-field keep: one series per field/zone

SPEC = [
    ("Air quality (measurement air_quality)", [
        (timeseries("Air temperature (°C)",
            flux("realtime", "air_quality", ["temperature_c"], K),
            unit="celsius", decimals=1,
            description="Air temperature per zone."), 12, 8),
        (timeseries("Air humidity (%)",
            flux("realtime", "air_quality", ["humidity_pct"], K),
            unit="humidity", decimals=1,
            description="Relative air humidity per zone."), 12, 8),
        (timeseries("CO₂ (ppm)",
            flux("realtime", "air_quality", ["co2_ppm"], K),
            unit="ppm", decimals=0,
            description="CO₂ concentration per zone."), 12, 8),
        (timeseries(f"Particulate PM2.5 / PM10 (µg/m³), PM2.5 threshold = {THRESHOLD_PM25}",
            flux("realtime", "air_quality", ["pm25_ugm3", "pm10_ugm3"], KF),
            decimals=1, threshold=(THRESHOLD_PM25, "red"),
            description="Fine dust; the red line is the SAME threshold evaluated by alert_pm25.flux in InfluxDB."), 12, 8),
    ]),
    ("Soil quality (measurement soil_quality)", [
        (timeseries("Soil pH",
            flux("realtime", "soil_quality", ["ph"], K),
            decimals=2, description="Soil acidity/alkalinity per zone."), 12, 8),
        (timeseries("Electrical conductivity EC (dS/m)",
            flux("realtime", "soil_quality", ["ec_dsm"], K),
            decimals=2, description="Soil salinity/EC per zone."), 12, 8),
        (timeseries("NPK nutrients (mg/kg)",
            flux("realtime", "soil_quality",
                 ["nitrogen_mgkg", "phosphorus_mgkg", "potassium_mgkg"], KF),
            decimals=0, description="Nitrogen, phosphorus and potassium in the soil."), 12, 8),
        (timeseries("Soil temperature (°C)",
            flux("realtime", "soil_quality", ["soil_temperature_c"], K),
            unit="celsius", decimals=1,
            description="Soil temperature per zone."), 12, 8),
    ]),
    ("Soil moisture (measurement soil_moisture)", [
        (timeseries(f"Soil moisture (%), water stress threshold = {THRESHOLD_SOIL}",
            flux("realtime", "soil_moisture", ["soil_moisture_pct"], K),
            unit="percent", decimals=1, threshold=(THRESHOLD_SOIL, "red"),
            description="Volumetric moisture; the red line is the SAME threshold evaluated by the water stress alert in InfluxDB."), 24, 7),
    ]),
    ("Zone actuators (measurement power_consumption)", [
        (timeseries("Actuator power (W)",
            flux("realtime", "power_consumption", ["power_w"],
                 ["_time", "_value", "actuator_id"]),
            unit=UNIT_WATT, decimals=1,
            description="Instantaneous consumption per actuator; the zone dashboards show only that zone's actuators."), 16, 9),
        (stat("Actuator status (last reading)",
            flux("realtime", "power_consumption", ["power_w"],
                 ["_time", "_value", "actuator_id", "status"],
                 range_expr="range(start: -2m)", last_by=["actuator_id"]),
            unit=UNIT_WATT, decimals=1,
            overrides=actuator_overrides(),
            description="Last power per actuator. RED only in an alarm condition (power_w > 1.5x that actuator's nominal power, like the overload alert); all 7 actuators are monitored, each with its own threshold."), 8, 9),
    ]),
    # Alerts row: Grafana does not evaluate conditions, it reads events already decided by the Flux tasks (bucket "alerts"); detection lives in the DBMS.
    ("Alerts (events detected by InfluxDB, bucket alerts)", [
        (alert_count_stat("Critical PM2.5", "pm25_critical",
            description="Number of alert_pm25 events (PM2.5 > 50 for 3 consecutive readings) in the period."), 8, 5),
        (alert_count_stat("Water stress", "soil_moisture_low",
            description="Number of water stress events (moisture < 20% for 2 consecutive readings) in the period."), 8, 5),
        (alert_count_stat("Electrical overload", "power_overload",
            description="Number of overload events (power_w > 1.5x nominal, actuator on) in the period."), 8, 5),
        (table("Alert events (most recent on top)",
            SEP.join([
                'from(bucket: "alerts")', RANGE_TR,
                'filter(fn: (r) => r._measurement == "alert_events")',
                'keep(columns: ["_time", "_value", "alert_type", "zone", "actuator_id"])',
                "group()",
                'sort(columns: ["_time"], desc: true)',
                "limit(n: 100)",
            ]),
            description="History of alert events written by the 3 Flux tasks; also queryable via CLI/API on the alerts bucket."), 24, 8),
    ]),
]

# Annotation: a red vertical line per event (bucket "alerts"); off by default (markers cluttered the charts) but re-enableable from the bar; from/range/... format for zone filter injection.
ALERT_ANNOTATION = {
    "datasource": DS,
    "enable": False,
    "hide": False,
    "iconColor": "red",
    "name": "Alerts (bucket alerts)",
    "target": {
        "query": SEP.join([
            'from(bucket: "alerts")', RANGE_TR,
            'filter(fn: (r) => r._measurement == "alert_events")',
            'map(fn: (r) => ({_time: r._time, text: "ALERT " + r.alert_type + (if exists r.zone then " zone " + r.zone else "") + (if exists r.actuator_id then " " + r.actuator_id else "")}))',
            "group()",
        ]),
        "refId": "Anno",
    },
}


def layout(spec):
    """Assign gridPos by walking rows and panels (wrap at width 24)."""
    panels = []
    y = 0
    for row_title, items in spec:
        r = row(row_title)
        r["gridPos"] = {"h": 1, "w": 24, "x": 0, "y": y}
        panels.append(r)
        y += 1
        x = 0
        rowmax = 0
        for panel, w, h in items:
            if x + w > 24:
                x = 0
                y += rowmax
                rowmax = 0
            panel["gridPos"] = {"h": h, "w": w, "x": x, "y": y}
            panels.append(panel)
            x += w
            rowmax = max(rowmax, h)
        y += rowmax
    return panels


def main():
    panels = layout(SPEC)
    dashboard = {
        "annotations": {"list": [{
            "builtIn": 1,
            "datasource": {"type": "grafana", "uid": "-- Grafana --"},
            "enable": True, "hide": True,
            "iconColor": "rgba(0, 211, 255, 1)",
            "name": "Annotations & Alerts", "type": "dashboard"},
            ALERT_ANNOTATION]},
        "editable": True, "fiscalYearStartMonth": 0, "graphTooltip": 1, "id": None,
        "links": [dict(NAV_LINK)],
        "panels": panels,
        "refresh": "10s", "schemaVersion": 39,
        "tags": ["smartfarm", "overview"],
        "templating": {"list": []},
        "time": {"from": "now-15m", "to": "now"}, "timepicker": {},
        "timezone": "browser",
        "title": "Smart Farm: zones and actuators monitoring",
        "uid": "smartfarm", "version": 1, "weekStart": "",
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dashboard, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"wrote {OUT}  ({len(panels)} panels)")


if __name__ == "__main__":
    main()
