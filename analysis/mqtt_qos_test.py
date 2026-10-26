"""MQTT QoS experiment on a local broker.

Test 1 - latency: 1000 telemetry-sized messages at 50 msg/s for QoS 0, 1 and 2.
Test 2 - link outage: the subscriber (dashboard) keeps a persistent session, goes offline
          for 3 s in the middle of a 200-message stream and reconnects; we count how many
          messages published during the outage are still delivered.
Writes results/mqtt_qos.csv and figures/mqtt_qos.png.
"""
import csv
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "figures"; RES = ROOT / "results"
FIG.mkdir(exist_ok=True); RES.mkdir(exist_ok=True)
PORT = 1885
TOPIC = "bms/pack01/qos_test"


def start_broker():
    if shutil.which("node") and (ROOT / "broker_node" / "node_modules" / "aedes").exists():
        cmd = ["node", str(ROOT / "broker_node" / "broker.js"), str(PORT), "9005"]
    else:
        cmd = [sys.executable, str(ROOT / "broker.py"), str(PORT)]
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL)
    for _ in range(50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", PORT)) == 0:
                return p
        time.sleep(0.2)
    raise RuntimeError("broker did not start")


def client(cid, clean=True):
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid, clean_session=clean)
    return c


def subscribe_and_wait(c, qos):
    """Connect, subscribe and block until the broker has acknowledged the subscription."""
    ready = threading.Event()
    c.on_subscribe = lambda *a: ready.set()
    c.connect("127.0.0.1", PORT); c.loop_start()
    c.subscribe(TOPIC, qos=qos)
    if not ready.wait(5):
        raise RuntimeError("no SUBACK from broker")


def payload(k):
    body = {"seq": k, "t_pub": time.time(), "pad": "x" * 560}     # ~620 B like telemetry
    return json.dumps(body)


def latency_test(qos, n=1000, rate=50):
    rx = []
    sub = client(f"lat-sub-{qos}")
    sub.on_message = lambda c, u, m: rx.append((json.loads(m.payload), time.time()))
    subscribe_and_wait(sub, qos)
    pub = client(f"lat-pub-{qos}"); pub.connect("127.0.0.1", PORT); pub.loop_start()
    time.sleep(0.5)
    for k in range(n):
        pub.publish(TOPIC, payload(k), qos=qos)
        time.sleep(1 / rate)
    time.sleep(1.0)
    for c in (pub, sub):
        c.loop_stop(); c.disconnect()
    lat = np.array([(t - p["t_pub"]) * 1000 for p, t in rx])
    return dict(qos=qos, sent=n, received=len(rx), mean_ms=lat.mean(), p50_ms=np.median(lat),
                p99_ms=np.percentile(lat, 99), max_ms=lat.max()), lat


def outage_test(qos, n=200, rate=20, off_at=50, off_s=3.0):
    rx = []
    cid = f"dash-persist-{qos}"
    sub = client(cid, clean=False)
    sub.on_message = lambda c, u, m: rx.append(json.loads(m.payload)["seq"])
    subscribe_and_wait(sub, qos)
    pub = client(f"out-pub-{qos}"); pub.connect("127.0.0.1", PORT); pub.loop_start()
    time.sleep(0.5)
    t_off = None
    offline = []
    for k in range(n):
        if k == off_at:
            sub.loop_stop(); sub.disconnect(); t_off = time.time()
        if t_off and time.time() - t_off < off_s:
            offline.append(k)
        elif t_off and not sub.is_connected():
            sub = client(cid, clean=False)
            sub.on_message = lambda c, u, m: rx.append(json.loads(m.payload)["seq"])
            sub.connect("127.0.0.1", PORT); sub.loop_start()
            t_off = -1
        pub.publish(TOPIC, payload(k), qos=qos)
        time.sleep(1 / rate)
    time.sleep(1.5)
    for c in (pub, sub):
        c.loop_stop(); c.disconnect()
    got = set(rx)
    lost_off = sum(1 for k in offline if k not in got)
    return dict(qos=qos, published=n, published_while_offline=len(offline),
                delivered=len(got), lost=n - len(got), lost_of_offline=lost_off,
                duplicates=len(rx) - len(got))


def wire_bytes(qos, topic_len=len(TOPIC), pl=620):
    rem = 2 + topic_len + (2 if qos else 0) + pl
    publish = 1 + (2 if rem > 127 else 1) + rem
    acks = {0: 0, 1: 4, 2: 12}[qos]                 # PUBACK / PUBREC+PUBREL+PUBCOMP
    return publish + acks, {0: 1, 1: 2, 2: 4}[qos]


def main():
    broker = start_broker()
    try:
        lat_rows, lats = [], {}
        for q in (0, 1, 2):
            r, lat = latency_test(q)
            r["bytes_on_wire"], r["packets_per_msg"] = wire_bytes(q)
            lat_rows.append(r); lats[q] = lat
            print("latency", {k: round(v, 3) if isinstance(v, float) else v for k, v in r.items()})
        out_rows = []
        for q in (0, 1, 2):
            r = outage_test(q)
            out_rows.append(r); print("outage ", r)
    finally:
        broker.terminate()
    with open(RES / "mqtt_qos.csv", "w", newline="") as f:
        w = csv.writer(f)
        keys = list(lat_rows[0]); w.writerow(keys)
        for r in lat_rows:
            w.writerow([round(r[k], 3) if isinstance(r[k], float) else r[k] for k in keys])
        w.writerow([]); keys = list(out_rows[0]); w.writerow(keys)
        for r in out_rows:
            w.writerow([r[k] for k in keys])

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.2))
    cols = ["#00798c", "#edae49", "#d1495b"]
    a1.boxplot([lats[q] for q in (0, 1, 2)], tick_labels=["QoS 0", "QoS 1", "QoS 2"],
               showfliers=False, patch_artist=True,
               boxprops=dict(facecolor="#cfe6ea"), medianprops=dict(color="k"))
    a1.set_ylabel("Publish -> subscriber latency (ms)")
    a1.set_title("(a) Latency, 1000 msgs @ 50/s", fontsize=9)
    x = np.arange(3)
    off = [r["published_while_offline"] for r in out_rows]
    lost = [r["lost_of_offline"] for r in out_rows]
    a2.bar(x - 0.18, off, 0.36, color="#bbbbbb", label="sent while offline")
    dlv = [o - l for o, l in zip(off, lost)]
    a2.bar(x + 0.18, dlv, 0.36, color="#00798c", label="delivered after reconnect")
    for xi, o, d in zip(x, off, dlv):
        a2.text(xi - 0.18, o + 1, str(o), ha="center", fontsize=8)
        a2.text(xi + 0.18, d + 1, str(d), ha="center", fontsize=8, color="#00798c", weight="bold")
    a2.set_ylim(0, max(off) * 1.35)
    a2.set_xticks(x); a2.set_xticklabels(["QoS 0", "QoS 1", "QoS 2"])
    a2.set_ylabel("Messages"); a2.legend(fontsize=7.5, loc="upper left")
    a2.set_title("(b) 3 s subscriber outage", fontsize=9)
    fig.tight_layout(); fig.savefig(FIG / "mqtt_qos.png", dpi=150); plt.close(fig)


if __name__ == "__main__":
    main()
