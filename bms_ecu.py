"""BMS ECU: measurement, SoC, protection logic and CAN frame generation."""
from collections import deque
from pathlib import Path

import cantools
import numpy as np

import config as C
from battery_sim import soc_from_ocv

DBC_PATH = Path(__file__).parent / "can_db" / "bms.dbc"


def load_db():
    return cantools.database.load_file(DBC_PATH)


def checksum(data, can_id):
    """0xFF XOR (sum of bytes 0..6 + low byte of CAN ID) - used on safety frames."""
    return 0xFF ^ ((sum(data[:7]) + (can_id & 0xFF)) & 0xFF)


class Debounce:
    """Condition must persist for `t_on` to set; clears after `t_off` of healthy."""

    def __init__(self, t_on, t_off=0.0, latching=False):
        self.t_on, self.t_off, self.latching = t_on, t_off, latching
        self.on_timer = self.off_timer = 0.0
        self.active = False

    def update(self, cond, dt):
        if cond:
            self.on_timer += dt; self.off_timer = 0.0
            if self.on_timer >= self.t_on - 1e-9:
                self.active = True
        else:
            self.on_timer = 0.0
            if self.active and not self.latching:
                self.off_timer += dt
                if self.off_timer >= self.t_off - 1e-9:
                    self.active = False
        return self.active

    def reset(self):
        self.active = False; self.on_timer = self.off_timer = 0.0


class BmsEcu:
    def __init__(self, seed=3):
        self.rng = np.random.default_rng(seed)
        self.db = load_db()
        self.t = 0.0
        self.soc = None
        self.contactor = 0              # 0 open, 1 precharge, 2 closed
        self.precharge_timer = 0.0
        self.state = 0
        self.counter = {}
        self.fault_count = 0
        self.balancing = np.zeros(C.N_SERIES, bool)
        self.temp_hist = deque(maxlen=61)   # 1 Hz history for dT/dt
        self.meas = {}
        names = C.FLAG_BITS
        self.warn = {n: Debounce(C.WARN_DEBOUNCE, 2.0) for n in names}
        self.fault = {n: Debounce(C.FAULT_DEBOUNCE, C.FAULT_AUTOCLEAR,
                                  latching=n in C.LATCHING_FAULTS) for n in names}
        self.fault["SHORT_CIRCUIT"] = Debounce(0.0, latching=True)   # instantaneous
        self.warn["TR_TREND"] = Debounce(2.0, latching=True)
        self.warn["IMBALANCE"] = Debounce(5.0, 10.0)        # judged at rest only
        self.warn_flags = self.fault_flags = 0
        self.flag_log = []              # (t, name, level, active)

    # ---------- measurement ----------
    def measure(self, v_cells, i, temps_true, sensor_open):
        v = np.round(v_cells + self.rng.normal(0, 0.002, v_cells.size), 3)   # 1 mV ADC
        i_m = i + self.rng.normal(0, 0.05)
        t_s = np.array([temps_true[k * C.CELLS_PER_SENSOR:(k + 1) * C.CELLS_PER_SENSOR].max()
                        for k in range(C.N_TEMP_SENSORS)])
        t_s = np.round(t_s + self.rng.normal(0, 0.1, t_s.size), 1)
        for k in range(C.N_TEMP_SENSORS):
            if sensor_open(k):
                t_s[k] = -40.0
        return v, i_m, t_s

    # ---------- main 100 ms task ----------
    def step(self, t, v_cells, i, temps_true, charger, sensor_open, reset=False):
        dt = C.DT_SIM
        self.t = t
        v, i_m, t_s = self.measure(v_cells, i, temps_true, sensor_open)
        if self.soc is None:                       # initialise SoC from rest OCV
            self.soc = float(np.mean(soc_from_ocv(v)))
        self.soc -= i_m * dt / (3600 * C.Q_NOM_AH * C.SOH_PACK / 100)
        self.soc = float(np.clip(self.soc, 0, 1))

        valid = (t_s > C.SENSOR_RANGE[0]) & (t_s < C.SENSOR_RANGE[1])
        t_valid = t_s[valid] if valid.any() else np.array([C.T_AMB])
        if abs(t * 10 - round(t * 10)) < 1e-6 and round(t * 10) % 10 == 0:
            self.temp_hist.append(np.where(valid, t_s, np.nan))
        rate = 0.0
        if len(self.temp_hist) == self.temp_hist.maxlen:
            rate = float(np.nanmax(self.temp_hist[-1] - self.temp_hist[0]))   # degC/min
        vmax, vmin = v.max(), v.min()
        tmax = t_valid.max()

        conds = {   # name: (warning condition, fault condition)
            "CELL_OV": (vmax > C.CELL_OV[0], vmax > C.CELL_OV[1]),
            "CELL_UV": (vmin < C.CELL_UV[0], vmin < C.CELL_UV[1]),
            "OC_DISCHARGE": (i_m > C.OC_DISCHARGE[0], i_m > C.OC_DISCHARGE[1]),
            "OC_CHARGE": (-i_m > C.OC_CHARGE[0], -i_m > C.OC_CHARGE[1]),
            "OVER_TEMP": (tmax > C.OVER_TEMP[0], tmax > C.OVER_TEMP[1]),
            "UNDER_TEMP": (t_valid.min() < C.UNDER_TEMP[0], t_valid.min() < C.UNDER_TEMP[1]),
            "IMBALANCE": (abs(i_m) < 1.0 and vmax - vmin > C.IMBALANCE_WARN, False),
            "LOW_SOC": (self.soc * 100 < C.LOW_SOC_WARN, False),
            "TR_TREND": (rate > C.TR_RATE_WARN and tmax > C.TR_MIN_TEMP, False),
            "SHORT_CIRCUIT": (False, i_m > C.SHORT_CIRCUIT),
            "SENSOR_FAULT": (not valid.all(), False),
        }
        if reset:
            for d in self.fault.values():
                d.reset()
            self.warn["TR_TREND"].reset()
        wf = ff = 0
        for name, (wc, fc) in conds.items():
            bit = 1 << C.FLAG_BITS[name]
            was_w, was_f = self.warn_flags & bit, self.fault_flags & bit
            if self.warn[name].update(wc, dt):
                wf |= bit
            if self.fault[name].update(fc, dt):
                ff |= bit
            if bool(wf & bit) != bool(was_w):
                self.flag_log.append((round(t, 1), name, "WARNING", bool(wf & bit)))
            if bool(ff & bit) != bool(was_f):
                self.flag_log.append((round(t, 1), name, "FAULT", bool(ff & bit)))
                if ff & bit:
                    self.fault_count += 1
        self.warn_flags, self.fault_flags = wf, ff

        # contactor and state machine
        if ff:
            self.contactor, self.precharge_timer = 0, 0.0
        elif self.contactor == 0 and t >= 1.0:
            self.contactor, self.precharge_timer = 1, 0.0
        elif self.contactor == 1:
            self.precharge_timer += dt
            if self.precharge_timer >= 1.0:
                self.contactor = 2
        latched = any(self.fault[n].active for n in C.LATCHING_FAULTS)
        if t < 0.5:
            self.state = 0
        elif latched:
            self.state = 6
        elif ff:
            self.state = 5
        elif wf:
            self.state = 4
        elif charger and i_m < -0.2:
            self.state = 3
        elif not charger and abs(i_m) > 0.2:
            self.state = 2                          # driving (incl. regenerative braking)
        else:
            self.state = 1

        # passive balancing during charge (bleed cells > min + 15 mV)
        self.balancing = (charger & (v > vmin + 0.015) & (vmax > 3.9)) if not ff else \
            np.zeros(C.N_SERIES, bool)

        # current limits with temperature and SoC derating
        dchg = C.OC_DISCHARGE[0] * np.clip((C.OVER_TEMP[1] - tmax) / 15, 0, 1) \
            * np.clip(self.soc / 0.10, 0, 1)
        chg = 7.5 * np.clip((C.OVER_TEMP[1] - tmax) / 15, 0, 1) * np.clip((4.20 - vmax) / 0.05, 0, 1)
        if ff:
            dchg = chg = 0.0
        self.meas = dict(v=v, i=i_m, temps=t_s, soc=self.soc, vmax=vmax, vmin=vmin,
                         tmax=tmax, tmin=t_valid.min(), dchg=dchg, chg=chg,
                         charger=charger, rate=rate)
        return self.contactor == 2

    # ---------- CAN ----------
    def _next_counter(self, name):
        c = self.counter.get(name, -1) + 1 & 0xF
        self.counter[name] = c
        return c

    def encode(self, name):
        m, db = self.meas, self.db
        msg = db.get_message_by_name(name)
        if name == "BMS_PackStatus":
            sig = dict(PackVoltage=float(m["v"].sum()), PackCurrent=float(np.clip(m["i"], -3276, 3276)),
                       SoC=round(m["soc"] * 200) / 2, SoH=C.SOH_PACK, BmsState=self.state,
                       RollingCounter=self._next_counter(name), Checksum=0)
        elif name == "BMS_CellSummary":
            sig = dict(MinCellVoltage=float(m["vmin"]), MaxCellVoltage=float(m["vmax"]),
                       MinCellIdx=int(np.argmin(m["v"])) + 1, MaxCellIdx=int(np.argmax(m["v"])) + 1,
                       MaxTemp=int(round(m["tmax"])), MinTemp=int(round(m["tmin"])))
        elif name == "BMS_Limits":
            sig = dict(DischargeCurrentLimit=round(float(m["dchg"]), 1),
                       ChargeCurrentLimit=round(float(m["chg"]), 1),
                       PackPower=int(round(m["v"].sum() * m["i"])), ContactorState=self.contactor,
                       ChargerConnected=int(m["charger"]), BalancingActive=int(self.balancing.any()))
        elif name.startswith("BMS_CellV_"):
            first = int(name[10:12])
            sig = {f"Cell{k:02d}": float(m["v"][k - 1]) for k in range(first, first + 4)}
        elif name == "BMS_Temperatures":
            sig = {f"Temp{k + 1}": float(m["temps"][k]) for k in range(C.N_TEMP_SENSORS)}
        elif name == "BMS_Faults":
            mask = int(sum(1 << k for k, b in enumerate(self.balancing) if b))
            sig = dict(WarningFlags=self.warn_flags, FaultFlags=self.fault_flags, BalancingMask=mask,
                       RollingCounter=self._next_counter(name), FaultCount=min(self.fault_count, 255),
                       Checksum=0)
        else:
            raise KeyError(name)
        data = bytearray(msg.encode(sig, strict=False))
        if "Checksum" in sig:
            data[7] = checksum(data, msg.frame_id)
        return msg.frame_id, bytes(data)
