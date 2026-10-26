"""Shared configuration for the Project 7 BMS communication / IoT pipeline.

Pack: 12S3P Li-ion (NMC 18650, 2.5 Ah per cell) -> 44.4 V nominal, 7.5 Ah, 333 Wh.
Each series element ("cell group", 3 cells in parallel) is modelled as one cell.
"""

# ---------------- Pack / cell model ----------------
N_SERIES = 12
N_TEMP_SENSORS = 4                 # one NTC per 3 series groups
CELLS_PER_SENSOR = N_SERIES // N_TEMP_SENSORS
Q_NOM_AH = 7.5                     # 3P x 2.5 Ah
R0_NOM = 0.013                     # ohm, ohmic resistance of a 3P group
R1_NOM = 0.008                     # ohm, polarisation resistance
TAU1 = 60.0                        # s, polarisation time constant
SOH_PACK = 96.0                    # %, reported state of health

# OCV-SoC table of an NMC cell (15 points)
SOC_PTS = [0, .02, .05, .10, .15, .20, .30, .40, .50, .60, .70, .80, .90, .95, 1.0]
OCV_PTS = [3.00, 3.20, 3.38, 3.48, 3.54, 3.58, 3.63, 3.67, 3.72, 3.79, 3.87, 3.95, 4.05, 4.11, 4.19]

# Lumped thermal model (per 3P group)
C_TH = 135.0                       # J/K, heat capacity (3 x 45 g x ~1 J/gK)
H_AMB = 0.25                       # W/K, convection to ambient
H_NEIGH = 0.30                     # W/K, conduction to neighbouring group
T_AMB = 30.0                       # degC

DT_SIM = 0.1                       # s, simulation step = fastest CAN cycle

# ---------------- BMS protection thresholds ----------------
#                 warning   fault
CELL_OV       = (4.20,     4.25)    # V
CELL_UV       = (3.10,     2.80)    # V
OC_DISCHARGE  = (25.0,     35.0)    # A (positive = discharge)
OC_CHARGE     = (12.0,     18.0)    # A (magnitude of negative current, incl. regen)
SHORT_CIRCUIT = 100.0               # A, instantaneous trip
OVER_TEMP     = (50.0,     60.0)    # degC
UNDER_TEMP    = (0.0,      -10.0)   # degC
IMBALANCE_WARN = 0.080              # V, max - min cell voltage
LOW_SOC_WARN  = 15.0                # %
TR_RATE_WARN  = 1.5                 # degC/min sustained temperature rise
TR_MIN_TEMP   = 40.0                # degC, rate check only above this
SENSOR_RANGE  = (-35.0, 120.0)      # degC, plausible NTC range
WARN_DEBOUNCE = 0.5                 # s
FAULT_DEBOUNCE = 1.0                # s
FAULT_AUTOCLEAR = 30.0              # s healthy before a non-latching fault clears

# Fault / warning bit positions (shared by BMS, gateway and dashboard)
FLAG_BITS = {
    "CELL_OV": 0, "CELL_UV": 1, "OC_DISCHARGE": 2, "OC_CHARGE": 3,
    "OVER_TEMP": 4, "UNDER_TEMP": 5, "IMBALANCE": 6, "LOW_SOC": 7,
    "TR_TREND": 8, "SHORT_CIRCUIT": 9, "SENSOR_FAULT": 10,
}
LATCHING_FAULTS = {"SHORT_CIRCUIT", "TR_TREND"}
FLAG_TEXT = {
    "CELL_OV": "Cell over-voltage", "CELL_UV": "Cell under-voltage",
    "OC_DISCHARGE": "Discharge over-current", "OC_CHARGE": "Charge over-current",
    "OVER_TEMP": "Over-temperature", "UNDER_TEMP": "Under-temperature",
    "IMBALANCE": "Cell imbalance", "LOW_SOC": "Low state of charge",
    "TR_TREND": "Thermal-runaway trend (dT/dt)", "SHORT_CIRCUIT": "Short circuit",
    "SENSOR_FAULT": "Temperature sensor fault",
    "COMM_LOSS": "BMS communication lost (CAN timeout)",
}

BMS_STATES = {0: "INIT", 1: "STANDBY", 2: "DISCHARGE", 3: "CHARGE",
              4: "WARNING", 5: "FAULT", 6: "SHUTDOWN"}

# ---------------- CAN ----------------
CAN_CHANNEL = "bms_vcan0"          # python-can virtual bus channel name
CAN_BITRATE = 500_000
# message name -> cycle time (s)
CAN_CYCLES = {
    "BMS_PackStatus": 0.1,
    "BMS_CellSummary": 0.1,
    "BMS_Limits": 0.1,
    "BMS_Faults": 0.1,
    "BMS_CellV_01_04": 0.2,
    "BMS_CellV_05_08": 0.2,
    "BMS_CellV_09_12": 0.2,
    "BMS_Temperatures": 1.0,
}
GATEWAY_TIMEOUT = 0.5              # s (sim time) without BMS_PackStatus -> comm-loss alarm

# ---------------- MQTT ----------------
MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
MQTT_WS_PORT = 9001
TOPIC_ROOT = "bms/pack01"
TELEMETRY_PERIOD = 1.0             # s (sim time) between telemetry publishes
