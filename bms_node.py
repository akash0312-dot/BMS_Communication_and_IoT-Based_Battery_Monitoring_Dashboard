"""BMS node: steps the pack model + BMS ECU and yields the CAN frames due each 100 ms.

Can be used standalone (offline, no bus) or by run_demo.py which puts the frames on
a python-can virtual bus.
"""
import numpy as np

import config as C
from battery_sim import PackModel, Scenario
from bms_ecu import BmsEcu


class BmsNode:
    def __init__(self, seed=7):
        self.pack = PackModel(seed)
        self.scn = Scenario()
        self.ecu = BmsEcu()
        self.k = 0
        self.last_fault_flags = 0
        self.truth = []          # ground-truth log, 1 Hz

    @property
    def t(self):
        return self.k * C.DT_SIM

    def step(self):
        """Advance 100 ms. Returns (t, list of (name, can_id, data))."""
        t = round(self.t, 1)
        pack, scn, ecu = self.pack, self.scn, self.ecu
        scn.apply_faults(t, pack)
        i_req, charger = scn.demand(t)
        i = i_req if ecu.contactor == 2 else 0.0
        v_cells = pack.cell_voltages(i)
        ecu.step(t, v_cells, i, pack.temp, charger,
                 lambda s: scn.sensor_open(t, s), reset=scn.service_reset(t))
        pack.bleed = np.where(ecu.balancing, 0.1, 0.0)
        pack.step(i)

        frames = []
        if not scn.comm_loss(t):
            ms = int(round(t * 1000))
            for name, cyc in C.CAN_CYCLES.items():
                if ms % int(cyc * 1000) == 0:
                    frames.append(name)
            if ecu.fault_flags | ecu.warn_flags << 16 != self.last_fault_flags \
                    and "BMS_Faults" not in frames:
                frames.append("BMS_Faults")              # event-triggered send
            self.last_fault_flags = ecu.fault_flags | ecu.warn_flags << 16
        out = [(n, *ecu.encode(n)) for n in frames]
        if self.k % 10 == 0:
            self.truth.append(dict(t=t, i=i, soc_true=float(np.mean(pack.z)),
                                   soc_bms=ecu.soc, vmin=float(v_cells.min()),
                                   vmax=float(v_cells.max()), tmax=float(pack.temp.max()),
                                   state=ecu.state, contactor=ecu.contactor))
        self.k += 1
        return t, out

    def done(self):
        return self.t >= self.scn.DURATION


if __name__ == "__main__":
    node = BmsNode()
    n_frames = 0
    while not node.done():
        _, fr = node.step()
        n_frames += len(fr)
    print("frames:", n_frames)
    for row in node.ecu.flag_log:
        print(row)
    tr = node.truth
    print("final SoC true %.3f bms %.3f, tmax %.1f" % (tr[-1]["soc_true"], tr[-1]["soc_bms"],
                                                      max(r["tmax"] for r in tr)))
    print("min vmin", min(r["vmin"] for r in tr), "max vmax", max(r["vmax"] for r in tr))
    print("SoC at 2200", [r for r in tr if r["t"] == 2200.0])
