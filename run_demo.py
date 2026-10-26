"""Run the complete pipeline:

  battery model + BMS ECU --(CAN, python-can virtual bus)--> gateway --(MQTT)--> broker
      --> web dashboard (WebSocket) / Node-RED (TCP) / recorder

Usage:
  python run_demo.py                  # real time, open http://localhost:8080 for the dashboard
  python run_demo.py --speed 20       # 20x faster than real time (50 min scenario in 2.5 min)
  python run_demo.py --speed 20 --trace --duration 3000
  python run_demo.py --external-broker   # use an already running broker on 1883/9001

The broker is Aedes (broker_node/, needs Node.js; `npm install` there once). amqtt
(broker.py) is used as a TCP-only fallback.
"""
import argparse
import csv
import functools
import http.server
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import can

import config as C
from bms_node import BmsNode
from gateway import CanMqttGateway
from recorder import Recorder

HERE = Path(__file__).parent


def port_in_use(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port, timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def serve_dashboard(port=8080):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HERE / "dashboard"))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", type=float, default=1.0, help="simulation speed-up factor")
    ap.add_argument("--duration", type=float, default=None, help="sim seconds (default: full)")
    ap.add_argument("--external-broker", action="store_true",
                    help="use a broker already running on 1883 (TCP) and 9001 (WebSocket)")
    ap.add_argument("--trace", action="store_true", help="write CAN trace results/can_trace.asc")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()
    out = HERE / args.out
    out.mkdir(exist_ok=True)

    broker = None
    port = C.MQTT_PORT
    if not args.external_broker and port_in_use(port):
        port = C.MQTT_PORT + 1                    # e.g. a Mosquitto service already on 1883
        print(f"Port {C.MQTT_PORT} is in use by another broker - starting ours on {port}")
    if not args.external_broker:
        if shutil.which("node") and (HERE / "broker_node" / "node_modules" / "aedes").exists():
            cmd = ["node", str(HERE / "broker_node" / "broker.js"), str(port), str(C.MQTT_WS_PORT)]
        else:
            print("Aedes broker not installed - falling back to amqtt (TCP only, no dashboard)")
            cmd = [sys.executable, str(HERE / "broker.py"), str(port)]
        broker = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.DEVNULL)
    if not wait_port(port):
        sys.exit(f"MQTT broker not reachable on port {port}")
    time.sleep(0.5)                               # WebSocket listener starts with TCP
    web = serve_dashboard()
    print("Dashboard: http://localhost:8080   (MQTT over WebSocket on port 9001)")

    node = BmsNode()
    duration = args.duration or node.scn.DURATION
    t_start = [None]

    def clock():
        # shared simulation clock, used to time-stamp alarms and the comm-loss watchdog
        # (real time is used for all latency measurements)
        return node.t

    recorder = Recorder(clock, port=port)
    gateway = CanMqttGateway(clock, port=port)
    gateway.start()
    bms_bus = can.Bus(interface="virtual", channel=C.CAN_CHANNEL)
    trace = can.ASCWriter(out / "can_trace.asc") if args.trace else None
    time.sleep(1.0)

    print(f"Running {duration:.0f} s scenario at {args.speed}x ...")
    t_start[0] = time.perf_counter()
    n_frames, last_print = 0, -1
    try:
        while node.t < duration:
            target = t_start[0] + node.t / args.speed
            delay = target - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            t, frames = node.step()
            for name, can_id, data in frames:
                msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=False,
                                  timestamp=time.time())
                bms_bus.send(msg)
                if trace:
                    trace.on_message_received(msg)
                n_frames += 1
            if int(t) // 100 != last_print:
                last_print = int(t) // 100
                m = node.ecu.meas
                print(f"  t={t:7.1f}s  V={m['v'].sum():6.2f}  I={m['i']:6.1f}  SoC={m['soc']*100:5.1f}%"
                      f"  Tmax={m['tmax']:5.1f}  state={C.BMS_STATES[node.ecu.state]}", flush=True)
    except KeyboardInterrupt:
        print("stopped by user")
    lag = time.perf_counter() - t_start[0] - node.t / args.speed
    print(f"done: {n_frames} CAN frames, real time {time.perf_counter() - t_start[0]:.1f} s "
          f"(final lag {lag * 1000:.0f} ms)")

    time.sleep(1.0)
    gateway.stop()
    recorder.stop()
    bms_bus.shutdown()
    if trace:
        trace.stop()
    recorder.save(out)
    with open(out / "bms_truth.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(node.truth[0]))
        w.writeheader(); w.writerows(node.truth)
    with open(out / "bms_flag_log.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "flag", "level", "active"]); w.writerows(node.ecu.flag_log)
    print("gateway stats:", gateway.stats)
    print(f"recorder: {len(recorder.telemetry)} telemetry msgs, {len(recorder.alarms)} alarms")
    web.shutdown()
    if broker:
        broker.terminate()


if __name__ == "__main__":
    main()
