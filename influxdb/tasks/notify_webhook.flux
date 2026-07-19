// Task 4: WEBHOOK notification of alert events, reads from the "alerts" bucket and does an HTTP POST to the alert-webhook service (a notification endpoint; persistence to MongoDB is done by the consumer).
// Separate task (not http.post inside the 3 detection tasks) for: (a) separating detection from notification, (b) URL and logic in one place, (c) if the webhook is down only the notification fails, not detection.
// At least once guarantee: the -2m lookback overlaps between runs (every 30s); duplicates are absorbed by the consumer idempotency (upsert on time+alert_type+zone+actuator_id); limit: if the webhook stays down beyond the lookback the events remain only in the "alerts" bucket.

import "http"
import "json"

option task = {name: "notify_webhook_alerts", every: 30s}

from(bucket: "alerts")
    // lookback 2m > every 30s: no event slips between two runs
    |> range(start: -2m)
    |> filter(fn: (r) => r._measurement == "alert_events")
    // One POST per event; "exists" because zone/actuator_id are tags present only on some alert_type (actuator_id only on power_overload).
    |> map(fn: (r) => ({r with http_status: http.post(
        // Docker service name as hostname (same rule as the whole pipeline)
        url: "http://alert-webhook:5000/alert",
        headers: {"Content-Type": "application/json"},
        data: json.encode(v: {
            time: r._time,
            alert_type: r.alert_type,
            zone: if exists r.zone then r.zone else "",
            actuator_id: if exists r.actuator_id then r.actuator_id else "",
            value: r._value,
        }))}))
