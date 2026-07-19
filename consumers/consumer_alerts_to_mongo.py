#!/usr/bin/env python3
"""Consumer: reads alerts from the InfluxDB "alerts" bucket and persists them to
MongoDB, independent of the webhook. An InfluxDB task writes alerts into the bucket
and also notifies the webhook; this consumer reads the bucket in parallel and stores
the events. Idempotent upsert on the natural key (time, alert_type, zone, actuator_id)
prevents duplicates on restart or retry. Polls every 10s."""

import time
import logging
from influxdb_client import InfluxDBClient
from pymongo import MongoClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# InfluxDB: host is the Docker service name, same token as Telegraf and Grafana.
INFLUX_URL = "http://influxdb:8086"
INFLUX_TOKEN = "agridata-admin-token-2026"
INFLUX_ORG = "agridata"
INFLUX_BUCKET = "alerts"

# MongoDB: host is the Docker service name, same DB as the webhook.
MONGO_URI = "mongodb://mongodb:27017"
MONGO_DB = "farm_history"
MONGO_COLL = "alert_history"

def connect_influx():
    """InfluxDB client with retry."""
    for attempt in range(5):
        try:
            client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
            client.ready()  # ping
            logger.info("InfluxDB connected")
            return client
        except Exception as e:
            logger.warning(f"InfluxDB attempt {attempt+1}/5 failed: {e}")
            time.sleep(2)
    raise RuntimeError("Cannot connect to InfluxDB after 5 attempts")

def connect_mongo():
    """MongoDB client with retry."""
    for attempt in range(5):
        try:
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
            client.admin.command('ping')
            logger.info("MongoDB connected")
            return client
        except Exception as e:
            logger.warning(f"MongoDB attempt {attempt+1}/5 failed: {e}")
            time.sleep(2)
    raise RuntimeError("Cannot connect to MongoDB after 5 attempts")

def query_alerts(influx_client, since_minutes=30):
    """Flux query: last N minutes from the "alerts" bucket (measurement alert_events).
    since_minutes=30 with a 10s poll gives a wide overlap, so no event is missed even
    with network delay or jitter. The resulting duplicates are absorbed by the idempotent
    upsert on the natural key. If the consumer stays down longer than the bucket retention
    (30d), the oldest history is lost, which is acceptable for the prototype."""
    query = f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{since_minutes}m)
  |> filter(fn: (r) => r._measurement == "alert_events")
"""
    try:
        query_api = influx_client.query_api()
        tables = query_api.query(query)
        alerts = []
        for table in tables:
            for record in table.records:
                # FluxRecord: record.values holds tags and fields, record.get_value() the _value.
                try:
                    record_dict = record.values
                    alert = {
                        "time": record_dict.get("_time", ""),
                        "alert_type": record_dict.get("alert_type", ""),
                        "zone": record_dict.get("zone") or "",
                        "actuator_id": record_dict.get("actuator_id") or "",
                        "value": record.get_value(),
                        "source": "influxdb",  # mark provenance for auditing
                    }
                    # Convert the timestamp to an ISO string if not already a string.
                    if hasattr(alert["time"], "isoformat"):
                        alert["time"] = alert["time"].isoformat()

                    # Keep only records with time and type (both required for the key).
                    if alert["time"] and alert["alert_type"]:
                        alerts.append(alert)
                except Exception as e:
                    logger.warning(f"Skip record: {e}")
                    continue
        return alerts
    except Exception as e:
        logger.error(f"InfluxDB query failed: {e}")
        return []

def save_to_mongo(mongo_coll, alerts):
    """Idempotent upsert on the natural key (time, alert_type, zone, actuator_id):
    no duplicates on restart or retry."""
    if not alerts:
        return

    written = 0
    for alert in alerts:
        try:
            filter_key = {
                "time": alert["time"],
                "alert_type": alert["alert_type"],
                "zone": alert.get("zone", ""),
                "actuator_id": alert.get("actuator_id", ""),
            }
            result = mongo_coll.update_one(filter_key, {"$set": alert}, upsert=True)
            if result.upserted_id or result.modified_count:
                written += 1
        except Exception as e:
            logger.error(f"Failed to save alert {alert}: {e}")

    if written > 0:
        logger.info(f"Saved {written}/{len(alerts)} alerts to MongoDB")

def main():
    """Main loop: read then save, every 10s."""
    influx = connect_influx()
    mongo = connect_mongo()

    try:
        mongo_coll = mongo[MONGO_DB][MONGO_COLL]
        # Unique index on the natural key: guarantees idempotency.
        mongo_coll.create_index([
            ("time", 1), ("alert_type", 1), ("zone", 1), ("actuator_id", 1)
        ], unique=True)
        logger.info(f"Unique index created on {MONGO_DB}.{MONGO_COLL}")

        logger.info("Consumer started. Polling every 10s...")
        while True:
            try:
                alerts = query_alerts(influx, since_minutes=30)
                save_to_mongo(mongo_coll, alerts)
            except Exception as e:
                logger.error(f"Cycle failed: {e}")

            time.sleep(10)

    except KeyboardInterrupt:
        logger.info("Consumer stopped by the user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        influx.close()
        mongo.close()
        logger.info("Connections closed")

if __name__ == "__main__":
    main()
