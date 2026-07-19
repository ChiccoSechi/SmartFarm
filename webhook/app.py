#!/usr/bin/env python3
"""Webhook notification endpoint: receives POSTs from the InfluxDB task and shows the
alerts read back from InfluxDB. Teaching prototype: Flask dev server, no auth, meant only
for the internal Docker network."""
from collections import Counter
from flask import Flask, request, render_template_string
from influxdb_client import InfluxDBClient
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# InfluxDB: Docker service name, same token as Telegraf and Grafana.
influx = InfluxDBClient(url="http://influxdb:8086", token="agridata-admin-token-2026", org="agridata")
query_api = influx.query_api()


# Labels and units shown to the user for each technical alert_type (from the Flux tasks).
LABELS = {
    "pm25_critical": "Critical PM2.5",
    "soil_moisture_low": "Water stress",
    "power_overload": "Electrical overload",
}
UNITS = {"pm25_critical": "µg/m³", "soil_moisture_low": "%",
         "power_overload": "W"}

# Single web page (Jinja2 via render_template_string); meta refresh every 5s auto-updates without JavaScript.
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>Smart Farm Alerts</title>
<style>
 body{font-family:system-ui,sans-serif;margin:2rem;color:#1a2b1a;background:#f6f8f4}
 h1{margin:0 0 .3rem}h1 small{font-weight:400;color:#6b7280;font-size:1rem}
 .badges{margin:.6rem 0 1.2rem}
 .badge{display:inline-block;padding:.25rem .7rem;border-radius:999px;
   color:#fff;font-size:.85rem;margin-right:.5rem}
 .pm25_critical{background:#d97706}.soil_moisture_low{background:#2563eb}
 .power_overload{background:#dc2626}
 table{border-collapse:collapse;width:100%;background:#fff;
   box-shadow:0 1px 3px rgba(0,0,0,.1)}
 th,td{padding:.5rem .8rem;text-align:left;border-bottom:1px solid #eee}
 th{background:#2f5233;color:#fff;font-weight:600}
 tbody tr{border-left:4px solid transparent}
 tbody tr.pm25_critical{border-left-color:#d97706}
 tbody tr.soil_moisture_low{border-left-color:#2563eb}
 tbody tr.power_overload{border-left-color:#dc2626}
 .foot{color:#6b7280;font-size:.8rem;margin-top:1rem}
 .empty{color:#6b7280;font-style:italic;margin-top:1rem}
</style></head><body>
<h1>Smart Farm Alerts <small>latest {{ total }} alerts (max 100)</small></h1>
<div class="badges">
{% for atype, n in per_type.items() %}
 <span class="badge {{ atype }}">{{ labels.get(atype, atype) }}: {{ n }}</span>
{% endfor %}
</div>
{% if latest %}
<table>
 <thead><tr><th>Time (UTC)</th><th>Type</th><th>Zone</th>
   <th>Actuator</th><th>Value</th></tr></thead>
 <tbody>
 {% for a in latest %}
  <tr class="{{ a.alert_type }}">
   <td>{{ a.time }}</td>
   <td>{{ labels.get(a.alert_type, a.alert_type) }}</td>
   <td>{{ a.zone or "-" }}</td>
   <td>{{ a.actuator_id or "-" }}</td>
   <td>{{ "%.1f"|format(a.value) }} {{ units.get(a.alert_type, "") }}</td>
  </tr>
 {% endfor %}
 </tbody>
</table>
{% else %}
<p class="empty">No alerts received yet: the simulator injects the anomalies on its own,
 wait a few minutes.</p>
{% endif %}
<p class="foot">Page auto-refreshed every 5s. Source: InfluxDB bucket "alerts" (direct Flux query).</p>
</body></html>"""


@app.get("/")
def index():
    """Home page (GET /): reads the last 100 alerts from the "alerts" bucket, newest first."""
    try:
        # group(columns: []) is required: sort() orders within each series (one per
        # tag combination alert_type/zone/actuator_id); without ungrouping, the order
        # would be per series and the table would look grouped by type.
        query = '''
from(bucket: "alerts")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "alert_events")
  |> group(columns: [])
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 100)
'''
        records = query_api.query(query)

        latest = []
        for table in records:
            for record in table.records:
                alert = {
                    "time": record.values.get("_time", ""),
                    "alert_type": record.values.get("alert_type", ""),
                    "zone": record.values.get("zone") or "",
                    "actuator_id": record.values.get("actuator_id") or "",
                    "value": record.get_value(),
                }
                if isinstance(alert["time"], str):
                    alert["time"] = alert["time"][:19]  # ISO string without microseconds
                latest.append(alert)

        total = len(latest)
        # Per-type summary computed on the records already read (no extra query):
        # the badges at the top show how many alerts per category.
        per_type = dict(Counter(a["alert_type"] for a in latest if a["alert_type"]))

    except Exception as e:
        logger.error(f"InfluxDB query failed: {e}")
        latest, total, per_type = [], 0, {}

    return render_template_string(
        PAGE, latest=latest, per_type=per_type,
        total=total, labels=LABELS, units=UNITS)


@app.post("/alert")
def alert():
    """Receives the notification from the InfluxDB task (audit logging only).
    Does not write to MongoDB: the consumer does. This is only a notification endpoint."""
    event = request.get_json(force=True)
    alert_type = event.get("alert_type", "unknown")
    value = event.get("value", "?")
    logger.info(f"Alert received: {alert_type} = {value} (persistence delegated to the consumer)")
    return {"ok": True}, 201


@app.get("/health")
def health():
    """Healthcheck: ping InfluxDB."""
    try:
        influx.ready()
        return {"ok": True, "influxdb": "connected"}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
