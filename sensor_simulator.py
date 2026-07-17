#!/usr/bin/env python3
"""Smart Farm sensor simulator: generates JSON readings from 4 sensor types over
3 zones, with injected anomalies, to stdout, CSV and MQTT."""

import argparse
import csv
import json
import os
import random
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

ZONES = ["A", "B", "C"]

ACTUATORS = [
    {"id": "irrigation_pump_1", "nominal_w": 750, "type": "pump", "zone": "A"},
    {"id": "irrigation_pump_2", "nominal_w": 750, "type": "pump", "zone": "B"},
    {"id": "irrigation_pump_3", "nominal_w": 750, "type": "pump", "zone": "C"},
    {"id": "greenhouse_fan_1", "nominal_w": 180, "type": "fan", "zone": "C"},
    {"id": "greenhouse_heater_1", "nominal_w": 2000, "type": "heater", "zone": "C"},
    {"id": "led_grow_light_1", "nominal_w": 400, "type": "lighting", "zone": "C"},
    {"id": "gateway_controller", "nominal_w": 15, "type": "gateway", "zone": None},
]

# Wide CSV schema: union of the columns of the 4 sensor types; irrelevant ones stay empty.
CSV_FIELDNAMES = [
    "timestamp", "sensor_id", "type", "zone",
    "actuator_id", "actuator_type", "status",
    "temperature_c", "humidity_pct", "co2_ppm", "pm25_ugm3", "pm10_ugm3",
    "ph", "ec_dsm", "nitrogen_mgkg", "phosphorus_mgkg", "potassium_mgkg",
    "soil_temperature_c", "soil_moisture_pct",
    "power_w", "voltage_v", "current_a",
]

STOP = False

def _handle_sigint(signum, frame):
    global STOP
    STOP = True


signal.signal(signal.SIGINT, _handle_sigint)
signal.signal(signal.SIGTERM, _handle_sigint)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# Per-zone state for realistic series: slow drift, noise, temporary anomaly events.

@dataclass
class ZoneState:
    zone: str
    temperature_c: float = field(default_factory=lambda: random.uniform(18, 26))
    humidity_pct: float = field(default_factory=lambda: random.uniform(45, 65))
    co2_ppm: float = field(default_factory=lambda: random.uniform(400, 550))
    pm25_ugm3: float = field(default_factory=lambda: random.uniform(5, 15))
    pm10_ugm3: float = field(default_factory=lambda: random.uniform(10, 25))
    ph: float = field(default_factory=lambda: random.uniform(6.0, 7.2))
    ec_dsm: float = field(default_factory=lambda: random.uniform(0.8, 1.6))
    n_mgkg: float = field(default_factory=lambda: random.uniform(20, 40))
    p_mgkg: float = field(default_factory=lambda: random.uniform(15, 30))
    k_mgkg: float = field(default_factory=lambda: random.uniform(100, 180))
    soil_temp_c: float = field(default_factory=lambda: random.uniform(15, 22))
    soil_moisture_pct: float = field(default_factory=lambda: random.uniform(35, 55))
    active_events: dict = field(default_factory=dict)


@dataclass
class ActuatorState:
    actuator_id: str
    nominal_w: float
    type: str
    zone: Optional[str]
    status: str = "off"
    ticks_in_state: int = 0
    overload_ticks_left: int = 0


def _clip(value, lo, hi):
    return max(lo, min(hi, value))


def maybe_start_event(state: ZoneState, name: str, probability: float, duration_range):
    if name not in state.active_events and random.random() < probability:
        state.active_events[name] = random.randint(*duration_range)


def tick_events(state: ZoneState):
    expired = []
    for name, ticks in state.active_events.items():
        ticks -= 1
        if ticks <= 0:
            expired.append(name)
        else:
            state.active_events[name] = ticks
    for name in expired:
        del state.active_events[name]


AIR_QUALITY_BASELINE = {
    "co2_ppm": 475.0,
    "pm25_ugm3": 10.0,
    "pm10_ugm3": 17.0,
}
AIR_QUALITY_REVERSION_RATE = 0.05  # reversion fraction toward baseline each cycle


def gen_air_quality(state: ZoneState) -> dict:
    state.temperature_c = _clip(state.temperature_c + random.uniform(-0.3, 0.3), 5, 40)
    state.humidity_pct = _clip(state.humidity_pct + random.uniform(-1.5, 1.5), 10, 95)
    state.co2_ppm += (AIR_QUALITY_BASELINE["co2_ppm"] - state.co2_ppm) * AIR_QUALITY_REVERSION_RATE
    state.pm25_ugm3 += (AIR_QUALITY_BASELINE["pm25_ugm3"] - state.pm25_ugm3) * AIR_QUALITY_REVERSION_RATE
    state.pm10_ugm3 += (AIR_QUALITY_BASELINE["pm10_ugm3"] - state.pm10_ugm3) * AIR_QUALITY_REVERSION_RATE

    state.co2_ppm = _clip(state.co2_ppm + random.uniform(-8, 8), 350, 2500)
    state.pm25_ugm3 = _clip(state.pm25_ugm3 + random.uniform(-1.5, 1.5), 1, 300)
    state.pm10_ugm3 = _clip(state.pm10_ugm3 + random.uniform(-2, 2), 2, 400)

    maybe_start_event(state, "pollution_spike", probability=0.005, duration_range=(6, 20))
    if "pollution_spike" in state.active_events:
        state.pm25_ugm3 = _clip(state.pm25_ugm3 + random.uniform(40, 90), 1, 300)
        state.pm10_ugm3 = _clip(state.pm10_ugm3 + random.uniform(50, 110), 2, 400)
        state.co2_ppm = _clip(state.co2_ppm + random.uniform(100, 300), 350, 2500)

    return {
        "sensor_id": f"air_zone{state.zone}",
        "type": "air_quality",
        "zone": state.zone,
        "timestamp": now_iso(),
        "temperature_c": round(state.temperature_c, 2),
        "humidity_pct": round(state.humidity_pct, 2),
        "co2_ppm": round(state.co2_ppm, 1),
        "pm25_ugm3": round(state.pm25_ugm3, 1),
        "pm10_ugm3": round(state.pm10_ugm3, 1),
    }


def gen_soil_quality(state: ZoneState) -> dict:
    state.ph = _clip(state.ph + random.uniform(-0.03, 0.03), 4.0, 9.0)
    state.ec_dsm = _clip(state.ec_dsm + random.uniform(-0.05, 0.05), 0.1, 5.0)
    state.n_mgkg = _clip(state.n_mgkg + random.uniform(-1, 1), 0, 100)
    state.p_mgkg = _clip(state.p_mgkg + random.uniform(-1, 1), 0, 100)
    state.k_mgkg = _clip(state.k_mgkg + random.uniform(-3, 3), 0, 300)
    state.soil_temp_c = _clip(state.soil_temp_c + random.uniform(-0.2, 0.2), 2, 35)

    return {
        "sensor_id": f"soil_quality_zone{state.zone}",
        "type": "soil_quality",
        "zone": state.zone,
        "timestamp": now_iso(),
        "ph": round(state.ph, 2),
        "ec_dsm": round(state.ec_dsm, 2),
        "nitrogen_mgkg": round(state.n_mgkg, 1),
        "phosphorus_mgkg": round(state.p_mgkg, 1),
        "potassium_mgkg": round(state.k_mgkg, 1),
        "soil_temperature_c": round(state.soil_temp_c, 2),
    }


def gen_soil_moisture(state: ZoneState, irrigation_active: bool) -> dict:
    state.soil_moisture_pct -= random.uniform(0.05, 0.25)

    maybe_start_event(state, "dry_spell", probability=0.02, duration_range=(20, 60))
    is_dry_spell = "dry_spell" in state.active_events
    if is_dry_spell:
        state.soil_moisture_pct -= random.uniform(0.3, 0.8)

    if irrigation_active:
        if is_dry_spell:
            state.soil_moisture_pct += random.uniform(0.2, 0.6)
        else:
            state.soil_moisture_pct += random.uniform(1.5, 3.5)

    state.soil_moisture_pct = _clip(state.soil_moisture_pct, 2, 95)

    return {
        "sensor_id": f"soil_moisture_zone{state.zone}",
        "type": "soil_moisture",
        "zone": state.zone,
        "timestamp": now_iso(),
        "soil_moisture_pct": round(state.soil_moisture_pct, 2),
    }


def gen_power_consumption(actuator: ActuatorState, zone_state: Optional[ZoneState]) -> dict:
    if actuator.type == "pump" and zone_state is not None:
        turn_on = zone_state.soil_moisture_pct < 30
    elif actuator.type == "gateway":
        turn_on = True
    else:
        if actuator.ticks_in_state > random.randint(10, 40):
            turn_on = random.random() < 0.5
            actuator.ticks_in_state = 0
        else:
            turn_on = actuator.status == "on"

    new_status = "on" if turn_on else "off"
    if new_status == actuator.status:
        actuator.ticks_in_state += 1
    else:
        actuator.ticks_in_state = 0
    actuator.status = new_status

    if actuator.status == "off":
        power_w = random.uniform(0, 2)
        voltage_v = round(random.uniform(228, 232), 1)
        current_a = round(power_w / voltage_v, 3)
    else:
        power_w = actuator.nominal_w * random.uniform(0.92, 1.05)

        if actuator.overload_ticks_left == 0 and random.random() < 0.04:
            actuator.overload_ticks_left = random.randint(4, 12)
        if actuator.overload_ticks_left > 0:
            power_w *= random.uniform(1.6, 2.3)
            actuator.overload_ticks_left -= 1

        voltage_v = round(random.uniform(215, 232), 1)
        current_a = round(power_w / voltage_v, 3)

    return {
        "sensor_id": f"power_{actuator.actuator_id}",
        "type": "power_consumption",
        "actuator_id": actuator.actuator_id,
        "actuator_type": actuator.type,
        "zone": actuator.zone,
        "timestamp": now_iso(),
        "status": actuator.status,
        "power_w": round(power_w, 1),
        "voltage_v": voltage_v,
        "current_a": current_a,
    }


def open_csv_writer(csv_path: str):
    """Open the CSV in append mode (creating dirs) and return (file, DictWriter);
    write the header only if the file is new or empty."""
    dirname = os.path.dirname(csv_path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)

    write_header = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
    f = open(csv_path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, restval="")
    if write_header:
        writer.writeheader()
        f.flush()
    return f, writer


def write_csv_row(writer: "csv.DictWriter", csv_file, payload: dict):
    writer.writerow(payload)
    csv_file.flush()  # so a tail on the file sees new rows immediately


# MQTT is the only channel to the pipeline: QoS 2, persistent session, fail fast.
# Topics: farm/{zone}/{type} and farm/actuators/{id}.

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")  # defaults to the Docker service name
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_QOS = 2                          # exactly once to the broker
MQTT_CLIENT_ID = "sensor-simulator"   # fixed id keeps the persistent session
MQTT_CONNECT_RETRIES = 10             # connect attempts at startup before giving up
MQTT_CONNECT_DELAY = 3                # seconds between attempts

_mqtt_client = None  # set by connect_mqtt() before the loop


def _mqtt_topic_for(payload: dict) -> str:
    """Topic from the payload: one per entity, selectable with a wildcard."""
    if payload.get("type") == "power_consumption":
        return f"farm/actuators/{payload['actuator_id']}"  # gateway has no zone
    return f"farm/{payload['zone']}/{payload['type']}"


def _create_mqtt_client():
    """Client compatible with paho 1.x and 2.x (2.x requires CallbackAPIVersion)."""
    import paho.mqtt.client as mqtt
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                           client_id=MQTT_CLIENT_ID, clean_session=False)
    except AttributeError:  # paho 1.x
        return mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=False)


def connect_mqtt():
    """Connect with retry and fail fast; loop_start() then reconnects on its own.
    Call once before the loop."""
    global _mqtt_client

    try:
        import paho.mqtt.client  # noqa: F401  fail fast if paho is missing
    except ImportError:
        print("# [mqtt] FATAL: paho-mqtt not installed "
              "(pip install paho-mqtt, or rebuild the Docker image).",
              file=sys.stderr)
        sys.exit(1)

    last_err = None
    for attempt in range(1, MQTT_CONNECT_RETRIES + 1):
        client = _create_mqtt_client()
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)  # raises if broker is down
            client.loop_start()  # network thread: ping and reconnect
            print(
                f"# [mqtt] connected to {MQTT_HOST}:{MQTT_PORT} "
                f"(QoS {MQTT_QOS}, persistent session)",
                file=sys.stderr,
            )
            _mqtt_client = client
            return client
        except Exception as exc:  # broker not ready, retry
            last_err = exc
            print(
                f"# [mqtt] broker not ready, attempt "
                f"{attempt}/{MQTT_CONNECT_RETRIES}, retrying in "
                f"{MQTT_CONNECT_DELAY}s ({exc})",
                file=sys.stderr,
            )
            try:
                client.disconnect()
            except Exception:
                pass
            time.sleep(MQTT_CONNECT_DELAY)

    # out of attempts: exit instead of staying silent
    print(
        f"# [mqtt] FATAL: cannot connect to "
        f"{MQTT_HOST}:{MQTT_PORT} after {MQTT_CONNECT_RETRIES} attempts "
        f"({last_err}). MQTT is the only pipeline channel, aborting.",
        file=sys.stderr,
    )
    sys.exit(1)


def publish_to_mqtt(payload: dict) -> None:
    """Publish the payload (QoS 2); warn if the broker rejects it, since MQTT is
    the only channel."""
    if _mqtt_client is None:  # should not happen (connect_mqtt fails fast)
        return
    topic = _mqtt_topic_for(payload)
    info = _mqtt_client.publish(topic, json.dumps(payload), qos=MQTT_QOS)
    if info.rc != 0:  # usually NO_CONN: broker unreachable
        print(f"# [mqtt] WARNING: publish not queued (rc={info.rc}) "
              f"on {topic}", file=sys.stderr)


def _shutdown_mqtt() -> None:
    """Shutdown: disconnect() before loop_stop(), so in-flight messages can leave."""
    global _mqtt_client
    if _mqtt_client is None:
        return
    try:
        _mqtt_client.disconnect()
        _mqtt_client.loop_stop()
    except Exception as exc:
        print(f"# [mqtt] shutdown error ({exc})", file=sys.stderr)
    finally:
        _mqtt_client = None


def emit(payload: dict, csv_file, csv_writer) -> None:
    print(json.dumps(payload), flush=True)
    write_csv_row(csv_writer, csv_file, payload)
    publish_to_mqtt(payload)


def main():
    parser = argparse.ArgumentParser(
        description="Smart Farm sensor simulator."
    )
    parser.add_argument("--interval", type=float, default=5.0,
                         help="Seconds between reading cycles (default: 5). "
                              "Controls both the stdout and CSV write cadence.")
    parser.add_argument("--csv-path", default="data/sensor_data.csv",
                         help="Path of the CSV file to append readings to (default: data/sensor_data.csv).")
    parser.add_argument("--duration", type=float, default=None,
                         help="Total simulation duration in seconds (default: infinite, Ctrl+C to stop).")
    parser.add_argument("--seed", type=int, default=None, help="Seed for deterministic generation.")

    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    # Startup diagnostics: which absolute CSV path the process uses and whether the dir is writable.
    abs_csv_path = os.path.abspath(args.csv_path)
    abs_dir = os.path.dirname(abs_csv_path) or "."
    print(f"# [diag] cwd: {os.getcwd()}", file=sys.stderr)
    print(f"# [diag] --csv-path: {args.csv_path!r}", file=sys.stderr)
    print(f"# [diag] absolute CSV path: {abs_csv_path}", file=sys.stderr)
    print(f"# [diag] directory writable? {os.access(abs_dir, os.W_OK) if os.path.isdir(abs_dir) else 'directory not created yet'}", file=sys.stderr)

    try:
        csv_file, csv_writer = open_csv_writer(args.csv_path)
    except OSError as exc:
        print(f"# [ERROR] cannot open/create the CSV file at {abs_csv_path!r}: {exc}", file=sys.stderr)
        sys.exit(1)

    zone_states = {z: ZoneState(zone=z) for z in ZONES}
    actuator_states = [
        ActuatorState(actuator_id=a["id"], nominal_w=a["nominal_w"], type=a["type"], zone=a["zone"])
        for a in ACTUATORS
    ]

    print(
        f"# Starting simulation | interval={args.interval}s csv={args.csv_path} "
        f"duration={'inf' if args.duration is None else args.duration}s",
        file=sys.stderr,
    )

    # Connect to MQTT eagerly and verify before the loop (fail fast): no point generating data nobody receives.
    connect_mqtt()

    start_time = time.time()
    tick = 0
    try:
        while not STOP:
            if args.duration is not None and (time.time() - start_time) >= args.duration:
                break

            for zone in ZONES:
                zs = zone_states[zone]
                tick_events(zs)

                emit(gen_air_quality(zs), csv_file, csv_writer)
                emit(gen_soil_quality(zs), csv_file, csv_writer)

                irrigation_active = any(
                    a.status == "on" for a in actuator_states
                    if a.type == "pump" and a.zone == zone
                )
                emit(gen_soil_moisture(zs, irrigation_active), csv_file, csv_writer)

            for actuator in actuator_states:
                zone_state = zone_states.get(actuator.zone) if actuator.zone else None
                emit(gen_power_consumption(actuator, zone_state), csv_file, csv_writer)

            tick += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        csv_file.close()
        _shutdown_mqtt()

    print(f"# Simulation stopped after {tick} cycles.", file=sys.stderr)


if __name__ == "__main__":
    main()
