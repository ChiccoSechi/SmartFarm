#!/usr/bin/env python3
"""Generate mappa.json: native Geomap panel (markers layer) with a satellite basemap,
fixed color nodes over Arborea (OR); coordinates are assigned by the Flux query from the
zone tag (single source of truth is this script)."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DASH_DIR = os.path.join(HERE, "provisioning", "dashboards")
OUT = os.path.join(DASH_DIR, "map.json")

# DS and NAV_LINK shared with the overview/zone generators (grafana/_common.py).
from _common import DS, NAV_LINK

# Realtime bucket: must match the one provisioned in influxdb.yml, otherwise no data.
BUCKET = "realtime"

# Single source of truth for the coordinates: Arborea (OR), plots about 500 m apart.
NODES = {
    # Node A east of C, same latitude but larger longitude (as requested).
    "A": {"lat": 39.7745, "lon": 8.5970},
    "B": {"lat": 39.7700, "lon": 8.5875},
    "C": {"lat": 39.7782, "lon": 8.5905},
}
# View center is the centroid of the nodes; zoom 14 frames all 3 plots.
VIEW_LAT = round(sum(n["lat"] for n in NODES.values()) / len(NODES), 5)
VIEW_LON = round(sum(n["lon"] for n in NODES.values()) / len(NODES), 5)
VIEW_ZOOM = 14


def _zone_case(value_for) -> str:
    """Flux chain `if r.zone == "A" then <v> else ...` built from NODES: value_for(zone)
    returns the already formatted expression for each zone. One place for the if/else logic,
    reused for coordinates (lat/lon) and for the zone dashboard uid."""
    zones = list(NODES)
    expr = value_for(zones[-1])                            # final else branch
    for z in reversed(zones[:-1]):
        expr = f'if r.zone == "{z}" then {value_for(z)} else ' + expr
    return expr


# lat/lon: node coordinates (single source is NODES). dash: zone dashboard uid
# (smartfarm-zone-<z>), consistent with gen_zone_dashboards.py, used by the marker data link.
LAT_EXPR = _zone_case(lambda z: str(NODES[z]["lat"]))
LON_EXPR = _zone_case(lambda z: str(NODES[z]["lon"]))
DASH_EXPR = _zone_case(lambda z: f'"smartfarm-zone-{z.lower()}"')

# Marker data link: opens the zone dashboard (uid computed in Flux), keeping the time range.
ZONE_LINK = {
    "title": "Open Zone ${__data.fields.zone} dashboard",
    "url": "/d/${__data.fields.dash}?${__url_time_range}",
    "targetBlank": False,
}

# Sensor selector (template variable `field`): fields are unique across measurements, so
# filtering by _field == "${field}" is enough (single query). Pairs (menu label, field); the first is the default.
METRICS = [
    ("Soil moisture (%)", "soil_moisture_pct"),
    ("Air temperature (°C)", "temperature_c"),
    ("Air humidity (%)", "humidity_pct"),
    ("CO₂ (ppm)", "co2_ppm"),
    ("PM2.5 (µg/m³)", "pm25_ugm3"),
    ("PM10 (µg/m³)", "pm10_ugm3"),
    ("Soil pH", "ph"),
    ("EC conductivity (dS/m)", "ec_dsm"),
    ("Nitrogen N (mg/kg)", "nitrogen_mgkg"),
    ("Phosphorus P (mg/kg)", "phosphorus_mgkg"),
    ("Potassium K (mg/kg)", "potassium_mgkg"),
    ("Soil temperature (°C)", "soil_temperature_c"),
]
DEFAULT_METRIC = METRICS[0]  # soil moisture


def field_variable():
    """Grafana `custom` variable (label to field) with explicit options/current so it works
    on first load (default = soil moisture)."""
    query = ", ".join(f"{label} : {field}" for label, field in METRICS)
    options = [{"text": label, "value": field, "selected": (label, field) == DEFAULT_METRIC}
               for label, field in METRICS]
    return {
        "name": "field",
        "label": "Sensor / measure",
        "type": "custom",
        "description": "Chooses which environmental measure colors and fills the nodes.",
        "query": query,
        "options": options,
        "current": {"selected": True, "text": DEFAULT_METRIC[0], "value": DEFAULT_METRIC[1]},
        "includeAll": False, "multi": False, "hide": 0, "skipUrlSync": False,
    }


# Common trunk of the two node queries (markers and table share everything but the map()
# body): filter by ${field}, group(zone)+last() (one row per node), group() (a single
# frame), keep, then the map() with the fields passed by the caller.
def _nodes_query(map_fields: str) -> str:
    """map_fields is the map() record body (already indented lines, no trailing comma)."""
    return (
        f'from(bucket: "{BUCKET}")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        '  |> filter(fn: (r) => r._field == "${field}")\n'
        "  |> filter(fn: (r) => exists r.zone)\n"
        '  |> group(columns: ["zone"])\n'
        "  |> last()\n"
        "  |> group()\n"
        '  |> keep(columns: ["_value", "zone"])\n'
        "  |> map(fn: (r) => ({\n"
        f"{map_fields}\n"
        "     }))"
    )


# Markers: value + coordinates + dashboard uid (data link). The value keeps the stable
# name `value` so color and data link do not change when `field` changes.
MARKERS_QUERY = _nodes_query(
    '       zone: r.zone,\n'
    '       node: "Node " + r.zone,\n'
    f"       dash: {DASH_EXPR},\n"
    "       value: r._value,\n"
    f"       latitude: {LAT_EXPR},\n"
    f"       longitude: {LON_EXPR}"
)

# Nodes table (no coordinates): zone and value, last reading per node, ordered by zone.
TABLE_QUERY = _nodes_query(
    '       zone: r.zone,\n'
    '       node: "Node " + r.zone,\n'
    "       value: r._value"
) + '\n  |> sort(columns: ["zone"])'


def geomap_panel():
    return {
        "datasource": DS,
        "description": (
            "Smart Farm environmental nodes on a satellite map (Arborea, OR). "
            "The displayed measure is chosen with the 'Sensor / measure' menu; nodes are "
            "fixed color dots (identical to each other). Exact value in the popup and "
            "in the table."
        ),
        "fieldConfig": {
            "defaults": {
                # Fixed marker color: dots locate the nodes, the value is read in the popup and the table.
                "color": {"mode": "fixed", "fixedColor": "dark-green"},
                "custom": {"hideFrom": {"legend": False, "tooltip": False, "viz": False}},
                "mappings": [],
                "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]},
                "decimals": 1,
            },
            "overrides": [
                # The value takes the chosen measure label as its name and carries the "open zone dashboard" data link.
                {"matcher": {"id": "byName", "options": "value"},
                 "properties": [{"id": "displayName", "value": "${field:text}"},
                                {"id": "links", "value": [ZONE_LINK]}]},
                # lat/lon/dash only position and link: hidden from the popup.
                {"matcher": {"id": "byName", "options": "latitude"},
                 "properties": [{"id": "custom.hideFrom",
                                 "value": {"legend": True, "tooltip": True, "viz": False}}]},
                {"matcher": {"id": "byName", "options": "longitude"},
                 "properties": [{"id": "custom.hideFrom",
                                 "value": {"legend": True, "tooltip": True, "viz": False}}]},
                {"matcher": {"id": "byName", "options": "dash"},
                 "properties": [{"id": "custom.hideFrom",
                                 "value": {"legend": True, "tooltip": True, "viz": False}}]},
            ],
        },
        "gridPos": {"h": 16, "w": 18, "x": 0, "y": 0},
        "id": 1,
        "options": {
            # Satellite basemap (native preset).
            "basemap": {
                "type": "esri-xyz",
                "name": "Satellite (ArcGIS World Imagery)",
                "config": {"server": "world-imagery"},
            },
            "controls": {
                "mouseWheelZoom": True, "showAttribution": True, "showDebug": False,
                "showMeasure": False, "showScale": False, "showZoom": True,
            },
            "layers": [{
                "type": "markers",
                "name": "Environmental nodes",
                "location": {"mode": "coords", "latitude": "latitude", "longitude": "longitude"},
                "config": {
                    # Legend off: with no color by value there is no scale to explain.
                    "showLegend": False,
                    "style": {
                        # Fixed color: no `field`, all nodes identical.
                        "color": {"fixed": "dark-green"},
                        "opacity": 0.9,
                        "rotation": {"fixed": 0, "max": 360, "min": -360, "mode": "mod"},
                        "size": {"fixed": 15, "max": 15, "min": 5},
                        "symbol": {"fixed": "img/icons/marker/circle.svg", "mode": "fixed"},
                        "symbolAlign": {"horizontal": "center", "vertical": "center"},
                        "text": {"field": "node", "fixed": "", "mode": "field"},
                        "textConfig": {"fontSize": 12, "offsetX": 0, "offsetY": -18,
                                       "textAlign": "center", "textBaseline": "middle"},
                    },
                },
                "tooltip": True,
            }],
            "tooltip": {"mode": "details"},
            "view": {"allLayers": True, "id": "coords",
                     "lat": VIEW_LAT, "lon": VIEW_LON, "zoom": VIEW_ZOOM},
        },
        "pluginVersion": "11.2.0",
        "targets": [{"datasource": DS, "query": MARKERS_QUERY, "refId": "A"}],
        "title": "Environmental nodes (${field:text}), Arborea (OR), Sardinia",
        "type": "geomap",
    }


def table_panel():
    return {
        "datasource": DS,
        "description": "Last value of the selected measure for each node (no coordinates).",
        "fieldConfig": {
            "defaults": {"color": {"mode": "thresholds"}, "custom": {
                "align": "auto", "cellOptions": {"type": "auto"}, "inspect": False},
                "mappings": [], "thresholds": {"mode": "absolute", "steps": [
                    {"color": "text", "value": None}]}},
            "overrides": [
                {"matcher": {"id": "byName", "options": "node"},
                 "properties": [{"id": "displayName", "value": "Node"}]},
                {"matcher": {"id": "byName", "options": "zone"},
                 "properties": [{"id": "displayName", "value": "Zone"}]},
                # Value column header = chosen measure label.
                {"matcher": {"id": "byName", "options": "value"},
                 "properties": [{"id": "displayName", "value": "${field:text}"},
                                {"id": "decimals", "value": 2}]},
            ],
        },
        "gridPos": {"h": 16, "w": 6, "x": 18, "y": 0},
        "id": 2,
        "options": {
            "showHeader": True,
            "cellHeight": "sm",
            "footer": {"show": False, "reducer": ["sum"], "countRows": False, "fields": ""},
        },
        "pluginVersion": "11.2.0",
        "targets": [{"datasource": DS, "query": TABLE_QUERY, "refId": "A"}],
        "title": "Nodes: ${field:text}",
        "type": "table",
        "transformations": [
            # Columns: Node, then value; 'zone' hidden (redundant with Node).
            {"id": "organize", "options": {
                "excludeByName": {"zone": True},
                "indexByName": {"node": 0, "value": 1},
                "renameByName": {}}},
        ],
    }


def main():
    dashboard = {
        "annotations": {"list": [{
            "builtIn": 1,
            "datasource": {"type": "grafana", "uid": "-- Grafana --"},
            "enable": True, "hide": True,
            "iconColor": "rgba(0, 211, 255, 1)",
            "name": "Annotations & Alerts", "type": "dashboard"}]},
        "editable": True, "fiscalYearStartMonth": 0, "graphTooltip": 1, "id": None,
        "links": [dict(NAV_LINK)],
        "panels": [geomap_panel(), table_panel()],
        "refresh": "10s", "schemaVersion": 39,
        "tags": ["smartfarm", "map"],
        "templating": {"list": [field_variable()]},
        "time": {"from": "now-15m", "to": "now"}, "timepicker": {},
        "timezone": "browser",
        "title": "Smart Farm: nodes map",
        "uid": "smartfarm-map", "version": 1, "weekStart": "",
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dashboard, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"wrote {OUT}  (uid={dashboard['uid']}, nodes={list(NODES)}, "
          f"view lat={VIEW_LAT} lon={VIEW_LON} zoom={VIEW_ZOOM})")


if __name__ == "__main__":
    main()
