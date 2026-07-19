// Alert 1: critical air, pm25_ugm3 > 50 for 3 consecutive readings per zone; stateCount() counts the consecutive ones in the DBMS, no custom code (the simulator injects "pollution_spike" for the demo).

option task = {name: "alert_pm25_critical", every: 30s}

from(bucket: "realtime")
    |> range(start: -2m)       // generous for 3 readings at 1 to 5s
    |> filter(fn: (r) => r._measurement == "air_quality" and r._field == "pm25_ugm3")
    |> group(columns: ["zone"])   // consecutive per zone
    |> sort(columns: ["_time"])
    |> stateCount(fn: (r) => r._value > 50.0)   // resets when the predicate becomes false
    |> filter(fn: (r) => r.stateCount >= 3)
    // record the event in the "alerts" bucket (alert history)
    |> map(fn: (r) => ({_time: r._time, _measurement: "alert_events", zone: r.zone, alert_type: "pm25_critical", value: r._value}))
    |> group()
    |> to(bucket: "alerts", tagColumns: ["zone", "alert_type"], fieldFn: (r) => ({"value": r.value}))
