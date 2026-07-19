#!/bin/bash
# InfluxDB alerting provisioning: runs once after setup, with the authenticated `influx` CLI (reproducible alerting on every start).
set -e

# Alert events bucket (30d retention: rare, lightweight events; permanent history in MongoDB via the consumer).
influx bucket create \
  --name alerts \
  --retention 30d \
  --org "${DOCKER_INFLUXDB_INIT_ORG}"

# load the alert tasks from the Flux files mounted by compose
for f in /etc/influxdb-tasks/*.flux; do
  echo "creating task from ${f}"
  influx task create --org "${DOCKER_INFLUXDB_INIT_ORG}" --file "${f}"
done
