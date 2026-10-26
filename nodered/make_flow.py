"""Generates nodered/flows.json - a Node-RED (node-red-dashboard) BMS dashboard that
subscribes to the gateway's MQTT topics.

    python nodered/make_flow.py [mqtt_port]
Import the file in Node-RED (Menu -> Import) and open http://localhost:1880/ui
"""
import json
import sys
from pathlib import Path

PORT = sys.argv[1] if len(sys.argv) > 1 else "1883"
nodes = []


def node(**kw):
    nodes.append(kw)
    return kw["id"]


TAB = node(id="f_bms", type="tab", label="BMS IoT Dashboard")
BROKER = node(id="mqtt_broker", type="mqtt-broker", name="Local broker", broker="127.0.0.1",
              port=PORT, clientid="nodered-bms", usetls=False, protocolVersion="4", keepalive="60",
              cleansession=True, birthTopic="", closeTopic="", willTopic="")
UI_TAB = node(id="ui_tab_bms", type="ui_tab", name="Battery Pack 01", icon="dashboard", order=1)
FONT = "-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Oxygen-Sans,Ubuntu,Cantarell,Helvetica Neue,sans-serif"
node(id="ui_base_cfg", type="ui_base", theme={
    "name": "theme-dark",
    "lightTheme": {"default": "#0094CE", "baseColor": "#0094CE", "baseFont": FONT, "edited": False},
    "darkTheme": {"default": "#097479", "baseColor": "#097479", "baseFont": FONT, "edited": True,
                  "reset": False},
    "customTheme": {"name": "Untitled Theme 1", "default": "#4B7930", "baseColor": "#4B7930",
                    "baseFont": FONT},
    "themeState": {k: {"value": v, "edited": False} for k, v in {
        "base-color": "#097479", "page-titlebar-backgroundColor": "#097479",
        "page-backgroundColor": "#111111", "page-sidebar-backgroundColor": "#333333",
        "group-textColor": "#0eb8c0", "group-borderColor": "#555555",
        "group-backgroundColor": "#333333", "widget-textColor": "#eeeeee",
        "widget-backgroundColor": "#097479", "widget-borderColor": "#333333",
        "base-font": FONT}.items()},
    "angularTheme": {"primary": "indigo", "accents": "blue", "warn": "red", "background": "grey",
                     "palette": "light"}},
     site={"name": "BMS Dashboard", "hideToolbar": "false", "allowSwipe": "false",
           "lockMenu": "false", "allowTempTheme": "true", "dateFormat": "DD/MM/YYYY",
           "sizes": {"sx": 48, "sy": 48, "gx": 6, "gy": 6, "cx": 6, "cy": 6, "px": 0, "py": 0}})
groups = {}
for k, (gid, name, width) in enumerate([("g_pack", "Pack", 6), ("g_trend", "Trends", 12),
                                        ("g_cells", "Cells and temperatures", 6),
                                        ("g_alarm", "Alarms", 12)]):
    groups[gid] = node(id=gid, type="ui_group", name=name, tab=UI_TAB, order=k + 1,
                       disp=True, width=str(width))

y = [40]


def place(**kw):
    kw.setdefault("z", TAB)
    kw.setdefault("x", 200 + 220 * kw.pop("col", 0))
    kw.setdefault("y", y[0])
    kw.setdefault("wires", [])
    return node(**kw)


# ---- telemetry ----
place(id="in_tel", type="mqtt in", name="bms/pack01/telemetry", topic="bms/pack01/telemetry",
      qos="0", datatype="json", broker=BROKER, wires=[["fn_split"]])
place(id="fn_split", type="function", name="split telemetry", col=1, outputs=8, func="""
const p = msg.payload;
const dv = p.vmax && p.vmin ? Math.round((p.vmax - p.vmin) * 1000) : null;
return [
  {payload: p.soc}, {payload: p.pack_v}, {payload: p.pack_i}, {payload: p.tmax},
  [{topic: "Voltage (V)", payload: p.pack_v}, {topic: "Current (A)", payload: p.pack_i}],
  p.temps.map((t, k) => ({topic: "T" + (k + 1), payload: t})),
  {payload: p.state + " | contactor " + p.contactor + " | t = " + p.t.toFixed(0) + " s"},
  {payload: "min " + p.vmin + " V (C" + p.vmin_idx + ")  max " + p.vmax + " V (C" + p.vmax_idx + ")  spread " + dv + " mV"}
];""", wires=[["ui_soc"], ["ui_v"], ["ui_i"], ["ui_t"], ["ui_chart_vi"], ["ui_chart_t"],
              ["ui_state"], ["ui_cells"]])
y[0] += 60
for k, (nid, label, unit, mn, mx, seg) in enumerate([
        ("ui_soc", "SoC", "%", 0, 100, (15, 30)), ("ui_v", "Pack voltage", "V", 30, 51, (37, 40)),
        ("ui_i", "Current", "A", -20, 40, (25, 35)), ("ui_t", "Max temperature", "degC", 0, 80, (50, 60))]):
    place(id=nid, type="ui_gauge", name=label, group=groups["g_pack"], order=k + 1, width="3",
          height="3", gtype="gage", title=label, label=unit, format="{{value}}", min=mn, max=mx,
          colors=["#00b500", "#e6e600", "#ca3838"] if nid not in ("ui_soc", "ui_v") else ["#ca3838", "#e6e600", "#00b500"],
          seg1=str(seg[0]), seg2=str(seg[1]), col=2, y=40 + 50 * k)
place(id="ui_chart_vi", type="ui_chart", name="V and I", group=groups["g_trend"], order=1,
      width="12", height="5", label="Pack voltage and current", chartType="line",
      removeOlder="10", removeOlderUnit="60", removeOlderPoints="600", ymin="", ymax="",
      legend="true", interpolate="linear", useOldStyle=False, col=2, y=240)
place(id="ui_chart_t", type="ui_chart", name="Temperatures", group=groups["g_cells"], order=2,
      width="6", height="4", label="Sensor temperatures (degC)", chartType="line",
      removeOlder="10", removeOlderUnit="60", removeOlderPoints="600", ymin="20", ymax="80",
      legend="true", interpolate="linear", useOldStyle=False, col=2, y=290)
place(id="ui_state", type="ui_text", name="state", group=groups["g_pack"], order=5, width="6",
      height="1", label="BMS", format="{{msg.payload}}", layout="row-spread", col=2, y=340)
place(id="ui_cells", type="ui_text", name="cells", group=groups["g_cells"], order=1, width="6",
      height="1", label="Cells", format="{{msg.payload}}", layout="row-spread", col=2, y=390)

# ---- alarms ----
y[0] = 480
place(id="in_alarm", type="mqtt in", name="bms/pack01/alarms", topic="bms/pack01/alarms",
      qos="1", datatype="json", broker=BROKER, wires=[["fn_alarm"]])
place(id="fn_alarm", type="function", name="alarm list + toast", col=1, outputs=2, func="""
const a = msg.payload;
let act = flow.get("active") || {};
let log = flow.get("log") || [];
const key = a.code + ":" + a.level;
if (a.active) act[key] = a; else delete act[key];
log.unshift(a); log = log.slice(0, 50);
flow.set("active", act); flow.set("log", log);
const toast = a.active && a.level === "FAULT"
  ? {topic: "BMS FAULT", payload: a.text + " at t = " + a.t.toFixed(1) + " s"} : null;
return [{payload: {active: Object.values(act), log: log}}, toast];""",
      wires=[["ui_alarms"], ["ui_toast"]])
place(id="ui_alarms", type="ui_template", name="alarm table", group=groups["g_alarm"], order=1,
      width="12", height="8", format="""
<div>
  <h4 style="margin:4px 0">Active alarms</h4>
  <div ng-if="!msg.payload.active.length" style="color:#3c3">No active alarms</div>
  <div ng-repeat="a in msg.payload.active" ng-style="{background: a.level=='FAULT' ? '#c33' : '#d98b00', color:'#fff', padding:'4px 8px', margin:'2px 0', 'border-radius':'4px'}">
    {{a.level}} - {{a.text}} (since t = {{a.t | number:1}} s, source {{a.source}})</div>
  <h4 style="margin:10px 0 4px">Alarm log</h4>
  <table style="width:100%; font-size:12px">
    <tr><th align="left">t (s)</th><th align="left">Level</th><th align="left">Alarm</th><th align="left">Status</th></tr>
    <tr ng-repeat="a in msg.payload.log"><td>{{a.t | number:1}}</td><td>{{a.level}}</td><td>{{a.text}}</td>
        <td>{{a.active ? 'RAISED' : 'cleared'}}</td></tr>
  </table>
</div>""", storeOutMessages=True, fwdInMessages=True, templateScope="local", col=2)
place(id="ui_toast", type="ui_toast", name="fault popup", position="top right", displayTime="5",
      highlight="red", outputs=0, topic="", col=2, y=530)
y[0] = 600
place(id="in_status", type="mqtt in", name="bms/pack01/status", topic="bms/pack01/status",
      qos="1", datatype="json", broker=BROKER, wires=[["fn_status"]])
place(id="fn_status", type="function", name="gateway status", col=1,
      func='msg.payload = msg.payload.online ? "ONLINE" : "OFFLINE"; return msg;', outputs=1,
      wires=[["ui_gw"]])
place(id="ui_gw", type="ui_text", name="gateway", group=groups["g_pack"], order=6, width="6",
      height="1", label="Gateway", format="{{msg.payload}}", layout="row-spread", col=2)

out = Path(__file__).with_name("flows.json")
out.write_text(json.dumps(nodes, indent=1))
print(f"wrote {out} ({len(nodes)} nodes, broker port {PORT})")
