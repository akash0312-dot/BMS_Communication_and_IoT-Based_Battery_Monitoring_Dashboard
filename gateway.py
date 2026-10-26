"""CAN -> MQTT gateway (telematics unit).

Receives BMS frames from the CAN bus, decodes them with the DBC, checks rolling
counters and checksums, detects communication loss, turns BMS flag changes into
alarm events and publishes JSON telemetry over MQTT.  Optionally forwards a subset
of the data to ThingSpeak when THINGSPEAK_API_KEY is set.
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request

import can
import paho.mqtt.client as mqtt

import config as C
from bms_ecu import checksum, load_db

T = C.TOPIC_ROOT
TOPIC_TELEMETRY = f"{T}/telemetry"
TOPIC_ALARMS = f"{T}/alarms"
TOPIC_STATUS = f"{T}/status"
TOPIC_GW_STATS = f"{T}/gateway"


class CanMqttGateway(threading.Thread):
    def __init__(self, clock, bus_kwargs=None, host=C.MQTT_HOST, port=C.MQTT_PORT):
        super().__init__(daemon=True, name="gateway")
        self.clock = clock                       # callable -> simulation time (s)
        self.db = load_db()
        self.bus = can.Bus(**(bus_kwargs or dict(interface="virtual", channel=C.CAN_CHANNEL)))
        self.mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="bms-can-gateway")
        self.mq.will_set(TOPIC_STATUS, json.dumps({"online": False}), qos=1, retain=True)
        self.mq.connect(host, port)
        self.mq.loop_start()
        self.stop_evt = threading.Event()
        self.sig = {}                            # latest decoded signal values
        self.t_can = {}                          # latest frame timestamp per message
        self.last_status_t = None
        self.last_pub_t = -1e9
        self.last_stats_t = 0.0
        self.comm_lost = False
        self.prev_warn = self.prev_fault = 0
        self.counter = {}
        self.stats = dict(frames=0, bytes=0, checksum_errors=0, counter_jumps=0,
                          comm_loss_events=0, telemetry_msgs=0, alarm_msgs=0)
        self.ts_key = os.environ.get("THINGSPEAK_API_KEY")
        self.ts_last = 0.0
        self._publish_status(True)

    # ---------------- MQTT helpers ----------------
    def _publish_status(self, online):
        self.mq.publish(TOPIC_STATUS, json.dumps({"online": online, "t": self.clock()}),
                        qos=1, retain=True)

    def _alarm(self, code, level, active, source, t_can):
        msg = dict(t=round(self.clock(), 2), code=code, level=level, active=active,
                   text=C.FLAG_TEXT.get(code, code.replace("_", " ").title()), source=source,
                   t_can=t_can, t_pub=time.time())
        self.mq.publish(TOPIC_ALARMS, json.dumps(msg), qos=1)
        self.stats["alarm_msgs"] += 1

    def _publish_telemetry(self):
        s = self.sig
        rnd = lambda x, n: None if x is None else round(float(x), n)
        cells = [rnd(s.get(f"Cell{k:02d}"), 3) for k in range(1, C.N_SERIES + 1)]
        temps = [rnd(s.get(f"Temp{k}"), 1) for k in range(1, C.N_TEMP_SENSORS + 1)]
        payload = dict(
            t=round(self.clock(), 2),
            pack_v=rnd(s.get("PackVoltage"), 2), pack_i=rnd(s.get("PackCurrent"), 1), soc=rnd(s.get("SoC"), 1),
            soh=s.get("SoH"), power_w=s.get("PackPower"),
            state=C.BMS_STATES.get(int(s.get("BmsState", 0)), "?"),
            contactor=["OPEN", "PRECHARGE", "CLOSED", "?"][int(s.get("ContactorState", 0))],
            charger=bool(s.get("ChargerConnected", 0)), balancing=int(s.get("BalancingMask", 0)),
            cells=cells, temps=temps, vmin=rnd(s.get("MinCellVoltage"), 3), vmax=rnd(s.get("MaxCellVoltage"), 3),
            vmin_idx=s.get("MinCellIdx"), vmax_idx=s.get("MaxCellIdx"),
            tmax=s.get("MaxTemp"), tmin=s.get("MinTemp"),
            dchg_limit=rnd(s.get("DischargeCurrentLimit"), 1), chg_limit=rnd(s.get("ChargeCurrentLimit"), 1),
            warn_flags=int(s.get("WarningFlags", 0)), fault_flags=int(s.get("FaultFlags", 0)),
            t_can=self.t_can.get("BMS_PackStatus"), t_pub=time.time(),
        )
        self.mq.publish(TOPIC_TELEMETRY, json.dumps(payload), qos=0)
        self.stats["telemetry_msgs"] += 1
        if self.ts_key and time.time() - self.ts_last >= 15.0:   # ThingSpeak free-tier limit
            self.ts_last = time.time()
            threading.Thread(target=self._thingspeak, args=(payload,), daemon=True).start()

    def _thingspeak(self, p):
        fields = dict(field1=p["pack_v"], field2=p["pack_i"], field3=p["soc"], field4=p["vmin"],
                      field5=p["vmax"], field6=p["tmax"], field7=p["warn_flags"],
                      field8=p["fault_flags"], api_key=self.ts_key)
        url = "https://api.thingspeak.com/update?" + urllib.parse.urlencode(fields)
        try:
            urllib.request.urlopen(url, timeout=5).read()
        except OSError as e:
            print("ThingSpeak upload failed:", e)

    # ---------------- CAN handling ----------------
    def _check_integrity(self, name, msg, dec):
        if "Checksum" in dec and checksum(msg.data, msg.arbitration_id) != msg.data[7]:
            self.stats["checksum_errors"] += 1
            return False
        if "RollingCounter" in dec:
            c = int(dec["RollingCounter"])
            prev = self.counter.get(name)
            if prev is not None and c != (prev + 1) & 0xF:
                self.stats["counter_jumps"] += 1
            self.counter[name] = c
        return True

    def handle(self, msg):
        try:
            m = self.db.get_message_by_frame_id(msg.arbitration_id)
        except KeyError:
            return
        dec = m.decode(msg.data, decode_choices=False)
        self.stats["frames"] += 1
        self.stats["bytes"] += len(msg.data)
        if not self._check_integrity(m.name, msg, dec):
            return
        self.sig.update(dec)
        self.t_can[m.name] = msg.timestamp
        now = self.clock()
        if m.name == "BMS_PackStatus":
            self.last_status_t = now
            if self.comm_lost:
                self.comm_lost = False
                self._alarm("COMM_LOSS", "FAULT", False, "GATEWAY", msg.timestamp)
            if now - self.last_pub_t >= C.TELEMETRY_PERIOD - 1e-6:
                self.last_pub_t = now
                self._publish_telemetry()
        elif m.name == "BMS_Faults":
            wf, ff = int(dec["WarningFlags"]), int(dec["FaultFlags"])
            for name, bit in C.FLAG_BITS.items():
                b = 1 << bit
                for level, new, old in (("WARNING", wf, self.prev_warn), ("FAULT", ff, self.prev_fault)):
                    if (new ^ old) & b:
                        self._alarm(name, level, bool(new & b), "BMS", msg.timestamp)
            self.prev_warn, self.prev_fault = wf, ff

    def _watchdog(self):
        now = self.clock()
        if self.last_status_t is not None and not self.comm_lost \
                and now - self.last_status_t > C.GATEWAY_TIMEOUT:
            self.comm_lost = True
            self.stats["comm_loss_events"] += 1
            self._alarm("COMM_LOSS", "FAULT", True, "GATEWAY", None)
        if now - self.last_stats_t >= 10.0:
            self.last_stats_t = now
            self.mq.publish(TOPIC_GW_STATS, json.dumps(dict(t=round(now, 1), **self.stats)))

    def run(self):
        while not self.stop_evt.is_set():
            msg = self.bus.recv(timeout=0.01)
            if msg is not None:
                self.handle(msg)
            else:
                self._watchdog()            # only when the queue is drained (no backlog)

    def stop(self):
        self.stop_evt.set()
        self.join(timeout=2)
        self._publish_status(False)
        time.sleep(0.2)
        self.mq.loop_stop()
        self.mq.disconnect()
        self.bus.shutdown()
