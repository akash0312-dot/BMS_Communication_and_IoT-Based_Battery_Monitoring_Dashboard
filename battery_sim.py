"""Battery data generator: 12S3P pack model, load profile and fault-injection scenario.

Each series group is a 1-RC Thevenin cell with its own capacity, resistance and
initial SoC, plus a lumped thermal node coupled to ambient and to its neighbours.
"""
import numpy as np

import config as C

_SOC = np.array(C.SOC_PTS)
_OCV = np.array(C.OCV_PTS)


def ocv(z):
    return np.interp(np.clip(z, 0.0, 1.0), _SOC, _OCV)


def soc_from_ocv(v):
    return np.interp(v, _OCV, _SOC)


class PackModel:
    """Physical 'true' pack. Positive current = discharge."""

    def __init__(self, seed=7):
        rng = np.random.default_rng(seed)
        n = C.N_SERIES
        self.q = C.Q_NOM_AH * rng.normal(1.0, 0.015, n)
        self.q[2] = 0.90 * C.Q_NOM_AH                   # cell 3: weak (aged) group
        self.r0 = C.R0_NOM * rng.normal(1.0, 0.05, n)
        self.r0[2] *= 1.25
        self.r1 = C.R1_NOM * np.ones(n)
        self.z = rng.normal(0.905, 0.006, n)            # initial SoC
        self.z[7] += 0.012                              # cell 8 slightly high
        self.v1 = np.zeros(n)
        self.temp = np.full(n, C.T_AMB)
        self.h_amb = np.full(n, C.H_AMB)
        self.extra_heat = np.zeros(n)                   # W, injected (internal short)
        self.self_discharge = np.zeros(n)               # A, injected leakage
        self.bleed = np.zeros(n)                        # A, balancing bleed current
        self.a1 = np.exp(-C.DT_SIM / C.TAU1)

    def cell_voltages(self, i):
        return ocv(self.z) - self.v1 - self.r0 * i

    def step(self, i):
        dt = C.DT_SIM
        i_cell = i + self.self_discharge + self.bleed
        v = self.cell_voltages(i)
        # heat: ohmic + polarisation + injected
        q_gen = i**2 * self.r0 + self.v1 * i + self.extra_heat
        t = self.temp
        q_amb = self.h_amb * (C.T_AMB - t)
        q_nb = np.zeros_like(t)
        q_nb[1:] += C.H_NEIGH * (t[:-1] - t[1:])
        q_nb[:-1] += C.H_NEIGH * (t[1:] - t[:-1])
        self.temp = t + dt * (q_gen + q_amb + q_nb) / C.C_TH
        self.z = self.z - i_cell * dt / (3600 * self.q)
        self.v1 = self.a1 * self.v1 + self.r1 * (1 - self.a1) * i
        return v


class Scenario:
    """Load profile plus a schedule of injected faults (times in sim seconds)."""

    DURATION = 3000.0

    # (name, onset, end, description) - used for the protection response table
    EVENTS = [
        ("REGEN_HIGH_SOC", 75.0, 81.0, "Strong regenerative braking at 90 % SoC (-14 A)"),
        ("OVERCURRENT", 600.0, 607.0, "Load surge: 30 A for 4 s, then 40 A"),
        ("CAN_COMM_LOSS", 1000.0, 1005.0, "BMS stops transmitting for 5 s"),
        ("SENSOR_OPEN", 1300.0, 1500.0, "NTC sensor 2 open circuit (reads -40 degC)"),
        ("SHORT_CIRCUIT", 1700.0, 1700.2, "External short: 150 A pulse"),
        ("SERVICE_RESET", 1760.0, 1760.1, "Operator resets latched fault"),
        ("THERMAL_ANOMALY", 2400.0, DURATION, "Module-3 fan failure + growing micro-short in cell 8 (2 W -> 16 W)"),
    ]

    def __init__(self, seed=11):
        self.rng = np.random.default_rng(seed)
        self.drive = self._drive_cycle()

    def _drive_cycle(self):
        """Urban drive-cycle-like current (A) at DT_SIM resolution, 60-2200 s."""
        seg = []  # (duration s, current A)
        rng = self.rng
        t = 0.0
        while t < 2140:
            peak = rng.uniform(19, 24.5)
            cruise = rng.uniform(10.5, 15.5)
            parts = [(rng.uniform(6, 12), peak), (rng.uniform(20, 45), cruise),
                     (rng.uniform(4, 8), -rng.uniform(4, 9)), (rng.uniform(5, 15), 0.6)]
            for d, a in parts:
                seg.append((d, a)); t += d
        cur = np.concatenate([np.full(int(d / C.DT_SIM), a) for d, a in seg])
        # first-order smoothing (motor controller current ramp)
        out = np.empty_like(cur); y = 0.0
        for k, c in enumerate(cur):
            y += (c - y) * 0.25; out[k] = y
        return out

    def demand(self, t):
        """Return (requested pack current A, charger_connected)."""
        if t < 60:
            return 0.0, False
        if 75 <= t < 81:
            return -14.0, False
        if 600 <= t < 604:
            return 30.0, False
        if 604 <= t < 607:
            return 40.0, False
        if 1700 <= t < 1700.2:
            return 150.0, False
        if t < 2200:
            k = int((t - 60) / C.DT_SIM)
            return float(self.drive[min(k, len(self.drive) - 1)]), False
        if t < 2300:
            return 0.0, False                      # parked, rest
        return -5.0, True                          # plug-in charging at 0.67C

    def apply_faults(self, t, pack):
        """Inject physical faults into the pack model."""
        if t >= 2400:
            pack.h_amb[6:9] = 0.06                 # module-3 fan failure
            # growing internal micro-short in cell 8 (early thermal-runaway trend)
            pack.extra_heat[7] = min(2.0 + 0.06 * (t - 2400), 16.0)
            pack.self_discharge[7] = 0.25

    def comm_loss(self, t):
        return 1000 <= t < 1005

    def sensor_open(self, t, k):
        return k == 1 and 1300 <= t < 1500

    def service_reset(self, t):
        return 1760 <= t < 1760 + C.DT_SIM
