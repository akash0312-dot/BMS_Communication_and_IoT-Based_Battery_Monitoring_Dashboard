"""MQTT subscriber that records everything the dashboard would see, with arrival
timestamps, so latency and alarm delivery can be analysed afterwards."""
import csv
import json
import threading
import time

import paho.mqtt.client as mqtt

import config as C


class Recorder:
    def __init__(self, clock, host=C.MQTT_HOST, port=C.MQTT_PORT):
        self.clock = clock
        self.telemetry, self.alarms, self.status, self.gw = [], [], [], []
        self.lock = threading.Lock()
        self.cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="bms-recorder")
        self.cli.on_message = self._on_message
        self.cli.on_connect = lambda c, u, f, rc, p: c.subscribe(f"{C.TOPIC_ROOT}/#", qos=1)
        self.cli.connect(host, port)
        self.cli.loop_start()

    def _on_message(self, cli, userdata, msg):
        t_rx = time.time()
        try:
            p = json.loads(msg.payload)
        except ValueError:
            return
        p["t_rx"] = t_rx
        p["t_sim_rx"] = self.clock()
        with self.lock:
            {"telemetry": self.telemetry, "alarms": self.alarms, "status": self.status,
             "gateway": self.gw}.get(msg.topic.rsplit("/", 1)[-1], []).append(p)

    def stop(self):
        time.sleep(0.5)
        self.cli.loop_stop()
        self.cli.disconnect()

    def save(self, outdir):
        with open(outdir / "mqtt_telemetry.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t", "pack_v", "pack_i", "soc", "power_w", "state", "contactor", "vmin",
                        "vmax", "tmax", "dchg_limit", "chg_limit", "warn_flags", "fault_flags",
                        "balancing", "latency_ms", "payload_bytes"]
                       + [f"cell{k + 1}" for k in range(C.N_SERIES)]
                       + [f"temp{k + 1}" for k in range(C.N_TEMP_SENSORS)])
            for p in self.telemetry:
                lat = (p["t_rx"] - p["t_can"]) * 1000 if p.get("t_can") else ""
                size = len(json.dumps({k: v for k, v in p.items() if k not in ("t_rx", "t_sim_rx")}))
                w.writerow([p["t"], p["pack_v"], p["pack_i"], p["soc"], p["power_w"], p["state"],
                            p["contactor"], p["vmin"], p["vmax"], p["tmax"], p["dchg_limit"],
                            p["chg_limit"], p["warn_flags"], p["fault_flags"], p["balancing"],
                            lat, size] + p["cells"] + p["temps"])
        with open(outdir / "mqtt_alarms.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_bms", "t_dashboard", "code", "level", "active", "source", "text",
                        "latency_ms"])
            for p in self.alarms:
                lat = (p["t_rx"] - p["t_can"]) * 1000 if p.get("t_can") else (p["t_rx"] - p["t_pub"]) * 1000
                w.writerow([p["t"], round(p["t_sim_rx"], 2), p["code"], p["level"], p["active"],
                            p["source"], p["text"], round(lat, 3)])
        if self.gw:
            with open(outdir / "gateway_stats.json", "w") as f:
                json.dump(self.gw[-1], f, indent=2)
