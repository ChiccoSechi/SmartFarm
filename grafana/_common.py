"""Constants shared by the three Grafana dashboard generators.
Single source of truth for the data source and the navigation link: avoids divergent
copies (change a tag or the uid here and it applies to all)."""

# Provisioned InfluxDB data source (grafana/provisioning/datasources/influxdb.yml).
DS = {"type": "influxdb", "uid": "influxdb-farm"}

# Dropdown listing the dashboards tagged "smartfarm" (navigation between the overview,
# the zone views and the map).
NAV_LINK = {
    "asDropdown": True, "icon": "external link", "includeVars": False,
    "keepTime": True, "tags": ["smartfarm"], "targetBlank": False,
    "title": "Go to zone / overview", "tooltip": "",
    "type": "dashboards", "url": "",
}
