// Alert 3: electrical overload, actuator on above 1.5x its nominal power, per actuator threshold on all 7 (15 W gateway vs 2000 W heater); nominal powers copied from ACTUATORS.

import "dict"

option task = {name: "alert_power_overload", every: 30s}

// nominal powers in watts, copied from ACTUATORS. All floats: the Flux dict wants one type.
nominal_w = ["irrigation_pump_1": 750.0, "irrigation_pump_2": 750.0, "irrigation_pump_3": 750.0, "greenhouse_heater_1": 2000.0, "greenhouse_fan_1": 180.0, "gateway_controller": 15.0, "led_grow_light_1": 400.0]

from(bucket: "realtime")
    // a single point is enough, no consecutive sequences
    |> range(start: -1m)
    |> filter(fn: (r) => r._measurement == "power_consumption" and r._field == "power_w")
    // only the ones that are on (status is a tag: no join)
    |> filter(fn: (r) => r.status == "on")
    // only actuators in the dictionary (default 0 means discarded)
    |> filter(fn: (r) => dict.get(dict: nominal_w, key: r.actuator_id, default: 0.0) > 0.0)
    // above 1.5x the nominal power
    |> filter(fn: (r) => r._value > 1.5 * dict.get(dict: nominal_w, key: r.actuator_id, default: 999999.0))
    // gateway has no zone: "if exists" avoids the missing column error
    |> map(fn: (r) => ({_time: r._time, _measurement: "alert_events", actuator_id: r.actuator_id, zone: if exists r.zone then r.zone else "n/a", alert_type: "power_overload", value: r._value}))
    |> group()
    |> to(bucket: "alerts", tagColumns: ["actuator_id", "zone", "alert_type"], fieldFn: (r) => ({"value": r.value}))
