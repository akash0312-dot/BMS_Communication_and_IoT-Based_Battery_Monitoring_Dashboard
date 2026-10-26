"""CAN frame/bus-load analysis, CRC error-detection test and BMS protocol comparison.

Outputs (figures/ and results/):
  can_message_table.csv   bit length (exact, with stuffing) and bus load per message
  bus_load_scaling.png    bus load vs number of series cells at 125/250/500 kbit/s
  error_detection.png     undetected-error probability: parity, checksum-8, CRC-15
  protocol_transfer.png   time to move one full BMS snapshot over each protocol
  protocol_metrics.csv
"""
import csv
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config as C                                  # noqa: E402
from bms_node import BmsNode                        # noqa: E402

FIG = ROOT / "figures"; FIG.mkdir(exist_ok=True)
RES = ROOT / "results"; RES.mkdir(exist_ok=True)
plt.rcParams.update({"font.size": 10, "axes.grid": True, "grid.alpha": 0.3, "figure.dpi": 150})

# ---------------------------------------------------------------- CAN bit level
CRC15_POLY = 0x4599                                 # x^15+x^14+x^10+x^8+x^7+x^4+x^3+1


def crc15(bits):
    crc = 0
    for b in bits:
        nxt = b ^ (crc >> 14 & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= CRC15_POLY
    return crc


def frame_bits(can_id, data):
    """Unstuffed bits SOF..CRC of a CAN 2.0A data frame."""
    bits = [0] + [can_id >> (10 - k) & 1 for k in range(11)] + [0, 0, 0]     # SOF, ID, RTR, IDE, r0
    bits += [len(data) >> (3 - k) & 1 for k in range(4)]
    for byte in data:
        bits += [byte >> (7 - k) & 1 for k in range(8)]
    crc = crc15(bits)
    return bits + [crc >> (14 - k) & 1 for k in range(15)]


def stuff_count(bits):
    n, run, last = 0, 0, None
    out = []
    for b in bits:
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
        if run == 5:
            stuff = 1 - b
            out.append(stuff); n += 1
            last, run = stuff, 1
    return n


def frame_length(can_id, data):
    """Total bits on the wire incl. stuff bits, CRC delimiter, ACK, EOF and 3-bit IFS."""
    b = frame_bits(can_id, data)
    return len(b) + stuff_count(b) + 1 + 2 + 7 + 3


def worst_case_length(n_bytes):
    return 47 + 8 * n_bytes + (34 + 8 * n_bytes - 1) // 4


def can_message_table():
    node = BmsNode()
    lengths = {}
    while node.t < 300:                              # 5 min of real frames
        _, frames = node.step()
        for name, cid, data in frames:
            lengths.setdefault((name, cid), []).append(frame_length(cid, data))
    rows, total = [], {125e3: 0, 250e3: 0, 500e3: 0}
    for (name, cid), ls in sorted(lengths.items(), key=lambda kv: kv[0][1]):
        cyc = C.CAN_CYCLES[name]
        mean = float(np.mean(ls))
        row = dict(message=name, can_id=f"0x{cid:03X}", dlc=8, cycle_ms=int(cyc * 1000),
                   bits_mean=round(mean, 1), bits_max=max(ls), bits_worst=worst_case_length(8),
                   frames_per_s=round(1 / cyc, 1))
        for br in total:
            load = mean / cyc / br * 100
            total[br] += load
            row[f"load_{int(br / 1000)}k_%"] = round(load, 3)
        rows.append(row)
    with open(RES / "can_message_table.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    worst500 = sum(worst_case_length(8) / C.CAN_CYCLES[r["message"]] for r in rows) / 500e3 * 100
    print("CAN message table:")
    for r in rows:
        print("  ", r)
    print("  total bus load %:", {int(k / 1000): round(v, 2) for k, v in total.items()},
          " worst-case stuffing @500k: %.2f %%" % worst500)
    return rows, total


def bus_load_scaling(rows):
    """Bus load vs number of series cells with the same message scheme."""
    mean_bits = np.mean([r["bits_mean"] for r in rows])
    cells = np.arange(12, 801, 4)
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    out = {}
    for br, col in ((125e3, "#d1495b"), (250e3, "#edae49"), (500e3, "#00798c")):
        fps = 4 / 0.1 + np.ceil(cells / 4) / 0.2 + np.ceil(np.ceil(cells / 3) / 4) / 1.0
        load = fps * mean_bits / br * 100
        ax.plot(cells, load, color=col, lw=2, label=f"{int(br / 1000)} kbit/s")
        limit = cells[load <= 50]
        out[br] = int(limit.max()) if limit.size else 0
    ax.axhline(50, color="k", ls="--", lw=1)
    ax.text(14, 52, "50 % design limit", fontsize=8)
    ax.axvline(12, color="grey", ls=":", lw=1)
    ax.text(20, 90, "this pack (12S)", fontsize=8, color="grey")
    ax.set_xlabel("Number of series cells reported"); ax.set_ylabel("CAN bus load (%)")
    ax.set_ylim(0, 100); ax.legend()
    ax.set_title("BMS bus load vs pack size (cell voltages every 200 ms)")
    fig.tight_layout(); fig.savefig(FIG / "bus_load_scaling.png"); plt.close(fig)
    print("  max cells below 50 % load:", {int(k / 1000): v for k, v in out.items()})
    return out


# ---------------------------------------------------------------- error detection
def crc15_remainder_int(e, nbits):
    """Remainder of the error polynomial (data+CRC field) - zero means undetected."""
    reg = 0
    for k in range(nbits - 1, -1, -1):
        reg = (reg << 1) | (e >> k & 1)
        if reg >> 15 & 1:
            reg ^= (1 << 15) | CRC15_POLY
    return reg


def error_detection(trials=40000, seed=1):
    rng = np.random.default_rng(seed)
    n_data = 64
    n_cw = 19 + n_data + 15                          # header + data + CRC bits covered by CRC
    ks = [1, 2, 3, 4, 5, 6, 7, 8]
    res = {"Byte parity (UART 8E1)": [], "8-bit sum checksum": [], "CAN CRC-15": []}
    for k in ks:
        und_par = und_sum = und_crc = 0
        for _ in range(trials):
            # parity: k flips inside 8 data bytes (+8 parity bits)
            pos = rng.choice(72, k, replace=False)
            per_byte = np.bincount(pos // 9, minlength=8)
            und_par += np.all(per_byte % 2 == 0)
            # 8-bit sum checksum over 7 data bytes + checksum byte
            data = rng.integers(0, 256, 7)
            cs = int(data.sum()) & 0xFF
            word = int.from_bytes(bytes(list(data) + [cs]), "big")
            pos = rng.choice(64, k, replace=False)
            for p in pos:
                word ^= 1 << int(p)
            b = word.to_bytes(8, "big")
            und_sum += (sum(b[:7]) & 0xFF) == b[7]
            # CRC-15: error polynomial over the whole codeword
            pos = rng.choice(n_cw, k, replace=False)
            e = 0
            for p in pos:
                e |= 1 << int(p)
            und_crc += crc15_remainder_int(e, n_cw) == 0
        res["Byte parity (UART 8E1)"].append(und_par / trials)
        res["8-bit sum checksum"].append(und_sum / trials)
        res["CAN CRC-15"].append(und_crc / trials)
        print(f"  {k} bit errors: parity {und_par/trials:.4f}  sum8 {und_sum/trials:.4f}  "
              f"crc15 {und_crc/trials:.6f}")
    # burst errors up to 15 bits are always detected by CRC-15
    burst_ok = all(crc15_remainder_int(((1 << L) - 1 | 1) << s | 1 << s, n_cw) != 0
                   for L in range(1, 15) for s in range(0, n_cw - L - 1))
    print("  CRC-15 detects every burst <= 15 bits:", burst_ok)

    fig, ax = plt.subplots(figsize=(6.6, 3.6))
    floor = 1 / trials
    x = np.arange(len(ks))
    for j, ((name, vals), col) in enumerate(zip(res.items(), ("#d1495b", "#edae49", "#00798c"))):
        xs = x + (j - 1) * 0.27
        v = np.array(vals)
        ax.bar(xs[v > 0], v[v > 0], 0.26, color=col, label=name)
        for xi in xs[v == 0]:
            ax.text(xi, floor * 0.55, "0", ha="center", va="top", fontsize=7, color=col, weight="bold")
    ax.set_yscale("log"); ax.set_ylim(floor * 0.25, 0.3)
    ax.axhline(2 ** -15, color="#00798c", ls="--", lw=1)
    ax.text(len(ks) - 0.5, 2 ** -15 * 1.25, "2$^{-15}$", fontsize=8, color="#00798c", ha="right")
    ax.set_xticks(x); ax.set_xticklabels(ks)
    ax.set_xlabel("Number of random bit errors in one frame")
    ax.set_ylabel("Fraction of errors NOT detected")
    ax.set_title(f"Undetected-error rate, {trials:,} corrupted frames per point ('0' = none missed)",
                 fontsize=9)
    ax.legend(fontsize=7.5, loc="upper right")
    fig.tight_layout(); fig.savefig(FIG / "error_detection.png"); plt.close(fig)
    with open(RES / "error_detection.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["bit_errors"] + list(res))
        for i, k in enumerate(ks):
            w.writerow([k] + [res[n][i] for n in res])
    return res, burst_ok


# ---------------------------------------------------------------- protocol comparison
SNAPSHOT_BYTES = 2 * C.N_SERIES + 2 * C.N_TEMP_SENSORS + 16    # cells + temps + status/faults = 48


def lora_airtime(pl, sf, bw=125e3, cr=1, n_pre=8, header=True, crc=True):
    de = 1 if (sf >= 11 and bw == 125e3) else 0
    t_sym = 2 ** sf / bw
    num = 8 * pl - 4 * sf + 28 + 16 * crc - 20 * (0 if header else 1)
    n_pl = 8 + max(math.ceil(num / (4 * (sf - 2 * de))) * (cr + 4), 0)
    return (n_pre + 4.25) * t_sym + n_pl * t_sym


def protocol_comparison():
    n = SNAPSHOT_BYTES
    frames = math.ceil(n / 8)
    can_bits = frames * 125                           # mean stuffed 8-byte frame (measured ~125)
    # CAN FD: arbitration phase 500k (~30 bits), data phase 2 Mbit/s (data + CRC-21 + control)
    fd_data_bits = 8 * n + 21 + 6 + 4 + 12 + (8 * n) // 8      # fixed + dynamic stuffing approx.
    t = {
        "CAN 2.0A\n500 kbit/s": can_bits / 500e3,
        "CAN FD\n0.5/2 Mbit/s": 30 / 500e3 + fd_data_bits / 2e6 + 10 / 500e3,
        "SMBus\n100 kHz": 2 * (9 * (3 + 1 + n // 2 + 1) + 3) / 100e3,     # 2 block reads + PEC
        "UART/RS-485\n115.2 kbit/s": (3 + n + 2) * 10 / 115200,             # header + CRC-16, 8N1
        "BLE 4.2\n1M PHY (air)": (1 + 4 + 2 + 4 + 3 + n + 3) * 8 / 1e6,     # one ATT notification
        "LoRa SF7\n125 kHz": lora_airtime(n + 13, 7),                       # + LoRaWAN header/MIC
        "LoRa SF12\n125 kHz": lora_airtime(n + 13, 12),
    }
    for k, v in t.items():
        print(f"  {k.replace(chr(10), ' '):28s} {v * 1000:9.3f} ms")
    fig, ax = plt.subplots(figsize=(6.6, 3.6))
    cols = ["#00798c", "#30638e", "#edae49", "#d1495b", "#8a4fd6", "#6a994e", "#386641"]
    bars = ax.bar(range(len(t)), [v * 1000 for v in t.values()], color=cols)
    ax.set_yscale("log"); ax.set_xticks(range(len(t))); ax.set_xticklabels(t.keys(), fontsize=7.5)
    for b, v in zip(bars, t.values()):
        ax.text(b.get_x() + b.get_width() / 2, v * 1000 * 1.15, f"{v * 1000:.2f}" if v < 1 else
                f"{v * 1000:.0f}", ha="center", fontsize=7.5)
    ax.set_ylabel("Transfer time (ms, log scale)")
    ax.set_title(f"Time on the link for one {n}-byte BMS snapshot")
    ax.set_ylim(0.1, 5000)
    fig.tight_layout(); fig.savefig(FIG / "protocol_transfer.png"); plt.close(fig)
    with open(RES / "protocol_metrics.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["protocol", "transfer_ms", "max_updates_per_s_at_1pct_duty"])
        for k, v in t.items():
            w.writerow([k.replace("\n", " "), round(v * 1000, 3),
                        round(0.01 / v, 3) if "LoRa" in k else ""])
    return t


if __name__ == "__main__":
    rows, total = can_message_table()
    bus_load_scaling(rows)
    error_detection()
    print(f"Protocol comparison for a {SNAPSHOT_BYTES}-byte snapshot:")
    protocol_comparison()
