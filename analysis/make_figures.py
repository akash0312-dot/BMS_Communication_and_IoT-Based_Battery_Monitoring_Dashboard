"""Figures and tables from a run_demo.py run (results/ -> figures/).

  telemetry_overview.png   pack V/I, SoC, cell min/max and temperatures as seen on the dashboard
  latency_hist.png         end-to-end CAN -> MQTT subscriber latency
  protection_table.csv     injected event -> BMS flag -> dashboard alarm timing
  payload_size.csv         bytes per update for different encodings
"""
import csv
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config as C                                    # noqa: E402
from battery_sim import Scenario                      # noqa: E402

RES = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "results")
FIG = ROOT / "figures"; FIG.mkdir(exist_ok=True)
plt.rcParams.update({"font.size": 9, "axes.grid": True, "grid.alpha": 0.3, "figure.dpi": 150})


def read(name):
    with open(RES / name) as f:
        return list(csv.DictReader(f))


def num(rows, key):
    return np.array([float(r[key]) if r[key] not in ("", "None") else np.nan for r in rows])


tel = read("mqtt_telemetry.csv")
alarms = read("mqtt_alarms.csv")
flags = read("bms_flag_log.csv")
truth = read("bms_truth.csv")
t = num(tel, "t")

# ------------------------------------------------------------ telemetry overview
fig, ax = plt.subplots(4, 1, figsize=(7.2, 8.2), sharex=True)
shade = {"OVERCURRENT": "#d1495b", "CAN_COMM_LOSS": "#555555", "SENSOR_OPEN": "#edae49",
         "SHORT_CIRCUIT": "#d1495b", "THERMAL_ANOMALY": "#e76f51", "REGEN_HIGH_SOC": "#30638e"}
for a in ax:
    for name, t0, t1, _ in Scenario.EVENTS:
        if name in shade:
            a.axvspan(t0, max(t1, t0 + 8), color=shade[name], alpha=0.15, lw=0)
ax[0].plot(t, num(tel, "pack_v"), color="#00798c", lw=1)
ax[0].set_ylabel("Pack voltage (V)")
a0 = ax[0].twinx(); a0.plot(t, num(tel, "pack_i"), color="#8a4fd6", lw=0.7, alpha=0.8)
a0.set_ylabel("Current (A)", color="#8a4fd6"); a0.grid(False)
ax[0].set_title("Telemetry received by the dashboard over MQTT (1 Hz)")
ax[1].plot(t, num(tel, "soc"), color="#00798c", lw=1.5, label="SoC reported by BMS")
ax[1].plot(num(truth, "t"), num(truth, "soc_true") * 100, "--", color="k", lw=0.8, label="True mean SoC")
ax[1].axhline(C.LOW_SOC_WARN, color="#edae49", ls=":", lw=1)
ax[1].set_ylabel("SoC (%)"); ax[1].legend(fontsize=7, loc="upper right")
ax[2].plot(t, num(tel, "vmax"), color="#d1495b", lw=0.7, label="max cell")
ax[2].plot(t, num(tel, "vmin"), color="#30638e", lw=0.7, label="min cell")
for thr, ls in ((C.CELL_OV[0], ":"), (C.CELL_UV[0], ":"), (C.CELL_UV[1], "--")):
    ax[2].axhline(thr, color="grey", ls=ls, lw=0.8)
ax[2].set_ylabel("Cell voltage (V)"); ax[2].set_ylim(2.6, 4.35); ax[2].legend(fontsize=7, loc="lower left")
for k, col in enumerate(["#2a9d8f", "#e9c46a", "#e76f51", "#6d597a"]):
    tk = num(tel, f"temp{k + 1}")
    ax[3].plot(t, np.where(tk < -30, np.nan, tk), color=col, lw=1, label=f"T{k + 1}")
ax[3].axhline(C.OVER_TEMP[0], color="#edae49", ls=":", lw=1)
ax[3].axhline(C.OVER_TEMP[1], color="#d1495b", ls="--", lw=1)
ax[3].set_ylabel("Temperature (°C)"); ax[3].legend(fontsize=7, ncol=4, loc="upper left")
ax[3].set_xlabel("Simulation time (s)")
for name, t0, _, _ in Scenario.EVENTS:
    if name in shade:
        ax[0].text(t0, ax[0].get_ylim()[1], name.replace("_", " ").title(), rotation=90,
                   fontsize=6, va="top", ha="right")
fig.tight_layout(); fig.savefig(FIG / "telemetry_overview.png"); plt.close(fig)

# ------------------------------------------------------------ latency
lat = num(tel, "latency_ms"); lat = lat[~np.isnan(lat)]
alat = np.array([float(a["latency_ms"]) for a in alarms if a["source"] == "BMS"])
fig, ax = plt.subplots(figsize=(6.4, 3.0))
ax.hist(lat, bins=60, color="#00798c", alpha=0.85, label=f"telemetry (n={lat.size})")
ax.hist(alat, bins=20, color="#d1495b", alpha=0.85, label=f"alarms (n={alat.size})")
ax.axvline(np.percentile(lat, 99), color="k", ls="--", lw=1)
ax.text(np.percentile(lat, 99), ax.get_ylim()[1] * 0.85, " p99", fontsize=8)
ax.set_xlabel("CAN frame on bus -> MQTT message at dashboard (ms)"); ax.set_ylabel("Count")
ax.set_title("End-to-end latency of the IoT pipeline"); ax.legend(fontsize=8)
fig.tight_layout(); fig.savefig(FIG / "latency_hist.png"); plt.close(fig)
lat_stats = dict(n=int(lat.size), mean=float(lat.mean()), p50=float(np.median(lat)),
                 p95=float(np.percentile(lat, 95)), p99=float(np.percentile(lat, 99)),
                 max=float(lat.max()), alarm_mean=float(alat.mean()), alarm_max=float(alat.max()))

# ------------------------------------------------------------ protection response table
EXPECT = {   # scenario event -> (flag, level) expected to be raised first, and BMS response
    "REGEN_HIGH_SOC": [("CELL_OV", "WARNING"), ("OC_CHARGE", "WARNING")],
    "OVERCURRENT": [("OC_DISCHARGE", "WARNING"), ("OC_DISCHARGE", "FAULT")],
    "CAN_COMM_LOSS": [("COMM_LOSS", "FAULT")],
    "SENSOR_OPEN": [("SENSOR_FAULT", "WARNING")],
    "SHORT_CIRCUIT": [("SHORT_CIRCUIT", "FAULT")],
    "THERMAL_ANOMALY": [("TR_TREND", "WARNING"), ("OVER_TEMP", "WARNING"), ("OVER_TEMP", "FAULT")],
}
RESPONSE = {"WARNING": "Warning state, alarm on dashboard", "FAULT": "Contactor opened, fault alarm"}
rows = []
for name, t0, t1, desc in Scenario.EVENTS:
    for flag, level in EXPECT.get(name, []):
        bms = next((float(f["t"]) for f in flags if f["flag"] == flag and f["level"] == level
                    and f["active"] == "True" and float(f["t"]) >= t0), None)
        dash = next((a for a in alarms if a["code"] == flag and a["level"] == level
                     and a["active"] == "True" and float(a["t_bms"]) >= t0 - 0.5), None)
        clr = next((a for a in alarms if a["code"] == flag and a["level"] == level
                    and a["active"] == "False" and dash and float(a["t_bms"]) > float(dash["t_bms"])), None)
        resp = RESPONSE[level] if flag != "COMM_LOSS" else "Gateway raises BMS-offline alarm"
        if flag == "SHORT_CIRCUIT":
            resp = "Contactor opened, latched until service reset"
        rows.append(dict(event=name, description=desc, injected_s=t0, flag=flag, level=level,
                         bms_detect_s=bms if bms is not None else (float(dash["t_bms"]) if dash else ""),
                         detection_delay_s=round((bms if bms is not None else float(dash["t_bms"])) - t0, 2)
                         if (bms is not None or dash) else "",
                         dashboard_latency_ms=dash["latency_ms"] if dash else "",
                         cleared_s=clr["t_bms"] if clr else "active at end", response=resp))
with open(RES / "protection_table.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

# ------------------------------------------------------------ payload sizes
sizes = num(tel, "payload_bytes")
frames_per_s = sum(1 / c for c in C.CAN_CYCLES.values())
payload = [
    ("Raw CAN frames forwarded (8 msgs x 8 B + 5 B header)", 8 * 13, "binary"),
    ("Compact binary snapshot (cells, temps, status)", 48, "binary"),
    ("JSON telemetry (this project)", float(np.mean(sizes)), "text"),
    ("One topic per signal (35 topics, ~6 B values)", 35 * (2 + 22 + 6 + 2), "text"),
]
with open(RES / "payload_size.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["encoding", "bytes_per_update", "type"]); w.writerows(payload)

summary = dict(latency_ms=lat_stats, telemetry_msgs=len(tel), alarm_msgs=len(alarms),
               can_frames_per_s=frames_per_s, mean_json_bytes=float(np.mean(sizes)))
gw = RES / "gateway_stats.json"
if gw.exists():
    summary["gateway"] = json.loads(gw.read_text())
(RES / "summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
for r in rows:
    print(r)
