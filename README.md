# Project 7 – BMS Communication and IoT-Based Battery Monitoring Dashboard

23RF16 Battery Management Systems – simulation-based mini project.

```
 battery_sim.py      bms_ecu.py / bms_node.py        gateway.py                broker            dashboard/index.html
 12S3P pack model -> BMS ECU (protection, SoC,  ->  CAN -> MQTT gateway  ->  Aedes MQTT  ->  web dashboard (WebSocket 9001)
 + fault scenario    DBC-encoded CAN frames)       (decode, checks,          1883/1884     Node-RED dashboard (TCP)
                     python-can virtual bus         alarms, JSON)                          recorder.py -> results/*.csv
```

## Files

| File | Purpose (team split) |
|---|---|
| `battery_sim.py` | Battery data generator: 12S3P Li-ion pack, 1-RC cell model, lumped thermal model, drive cycle, fault injection schedule |
| `bms_ecu.py`, `bms_node.py` | BMS ECU: measurement noise, SoC, warning/fault detection with debounce, contactor state machine, CAN frame encoding |
| `can_db/bms.dbc` | CAN message design (8 messages, IDs 0x100–0x130, rolling counter + checksum) |
| `gateway.py` | CAN → MQTT pipeline: DBC decoding, checksum/counter checks, comm-loss watchdog, alarms, JSON telemetry, optional ThingSpeak upload |
| `broker_node/broker.js` | Local MQTT broker (Aedes) with TCP and WebSocket listeners |
| `broker.py` | Fallback Python broker (amqtt), TCP only |
| `dashboard/index.html` | Web dashboard (Chart.js + MQTT.js) with alarms, banner and beep on faults |
| `nodered/flows.json` | Node-RED dashboard flow (gauges, charts, alarm table, fault toast) |
| `recorder.py` | Records everything the dashboard receives, with timestamps |
| `run_demo.py` | Starts broker, gateway, recorder and dashboard server and runs the scenario |
| `analysis/can_protocol_analysis.py` | CAN bit-level frame length, bus load, CRC-15 Monte Carlo, CAN/SMBus/UART/BLE/LoRa comparison |
| `analysis/mqtt_qos_test.py` | MQTT QoS 0/1/2 latency and link-outage test |
| `analysis/make_figures.py` | Figures and tables for the report |

## Setup

```bash
pip install -r requirements.txt
```

```bash
cd broker_node && npm install
```

## Run

```bash
python run_demo.py
```

Open http://localhost:8080 for the dashboard. The 50-minute scenario runs in real time; use
`--speed 10` to run it ten times faster, `--trace` to save the CAN trace (`results/can_trace.asc`).
If port 1883 is already used (e.g. an installed Mosquitto service) the demo starts its broker on 1884.

Node-RED (optional): `npm install -g node-red`, then in the Node-RED palette manager install
`node-red-dashboard`, import `nodered/flows.json` (regenerate it for port 1884 with
`python nodered/make_flow.py 1884` if needed), deploy, and open http://localhost:1880/ui.

ThingSpeak (optional): set `THINGSPEAK_API_KEY` to a channel's write key before `run_demo.py`;
the gateway then uploads 8 fields every 15 s.

## Analysis

```bash
python analysis/can_protocol_analysis.py
```

```bash
python analysis/mqtt_qos_test.py
```

```bash
python analysis/make_figures.py
```
