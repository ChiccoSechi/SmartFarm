// Alert 2: water stress, soil_moisture_pct < 20% for 2 consecutive readings per zone; same stateCount pattern as the PM2.5 alert (the simulator injects "dry_spell").

option task = {name: "alert_water_stress", every: 30s}

from(bucket: "realtime")
    |> range(start: -2m)
    |> filter(fn: (r) => r._measurement == "soil_moisture" and r._field == "soil_moisture_pct")
    |> group(columns: ["zone"])   // consecutive per zone
    |> sort(columns: ["_time"])
    |> stateCount(fn: (r) => r._value < 20.0)
    |> filter(fn: (r) => r.stateCount >= 2)
    |> map(fn: (r) => ({_time: r._time, _measurement: "alert_events", zone: r.zone, alert_type: "soil_moisture_low", value: r._value}))
    |> group()
    |> to(bucket: "alerts", tagColumns: ["zone", "alert_type"], fieldFn: (r) => ({"value": r.value}))
