"""Block diagram of the simulated BMS communication / IoT system -> figures/architecture.png"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

FIG = Path(__file__).resolve().parents[1] / "figures"; FIG.mkdir(exist_ok=True)
fig, ax = plt.subplots(figsize=(7.6, 3.9))
ax.set_xlim(0, 100); ax.set_ylim(0, 52); ax.axis("off")


def box(x, y, w, h, title, lines, color):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.2",
                                fc=color, ec="#333333", lw=1))
    ax.text(x + w / 2, y + h - 2.2, title, ha="center", va="top", fontsize=8.2, weight="bold")
    ax.text(x + w / 2, y + h - 3.4 - 3.4 * (title.count("\n") + 1), "\n".join(lines), ha="center", va="top", fontsize=6.6,
            linespacing=1.35)


def arrow(x0, y0, x1, y1, label="", dy=1.6):
    ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=11,
                                 lw=1.3, color="#333333"))
    if label:
        ax.text((x0 + x1) / 2, (y0 + y1) / 2 + dy, label, ha="center", fontsize=6.6, style="italic")


box(1, 27, 19, 22, "Battery data\ngenerator", ["12S3P Li-ion pack", "1-RC cell model", "thermal model",
                                                "drive cycle +", "fault injection"], "#e3f1f4")
box(25, 27, 19, 22, "BMS ECU", ["noisy measurement", "SoC, limits", "warning / fault", "state machine",
                                 "DBC encoder"], "#e7eefa")
box(49, 27, 19, 22, "CAN-MQTT\ngateway", ["DBC decoder", "checksum, counter", "comm-loss watchdog",
                                           "alarm events", "JSON @ 1 Hz"], "#f3ecfb")
box(73, 27, 19, 22, "MQTT broker", ["Aedes", "TCP 1883/1884", "WebSocket 9001", "QoS 0 / 1, retain",
                                     "last will"], "#fdf3e1")
box(53, 2, 14, 15, "Web dashboard", ["Chart.js + MQTT.js", "alarms, banner"], "#e6f4ea")
box(70, 2, 14, 15, "Node-RED", ["gauges, charts", "alarm table, toast"], "#e6f4ea")
box(87, 2, 12, 15, "Recorder /\nThingSpeak", ["CSV logs", "cloud (opt.)"], "#e6f4ea")
arrow(21, 38, 24.4, 38, "V, I, T")
arrow(45, 38, 48.4, 38, "CAN 500k")
ax.text(46.7, 33.5, "8 msgs\n56 fr/s", ha="center", fontsize=6)
arrow(69, 38, 72.4, 38, "MQTT")
arrow(30, 26.4, 30, 21, "")
ax.text(30, 18.5, "contactor command\n(open on fault)", ha="center", fontsize=6.4)
ax.add_patch(FancyArrowPatch((25, 20), (11, 26.4), arrowstyle="-|>", mutation_scale=10, lw=1,
                             color="#d1495b", connectionstyle="arc3,rad=0.25"))
for x in (60, 77, 93):
    ax.add_patch(FancyArrowPatch((82.5, 26.4), (x, 17.6), arrowstyle="-|>", mutation_scale=10,
                                 lw=1.1, color="#333333"))
ax.text(1, 0.5, "python-can virtual bus (channel bms_vcan0)", fontsize=6.4, color="#555555")
fig.tight_layout(); fig.savefig(FIG / "architecture.png", dpi=200); plt.close(fig)
print("saved", FIG / "architecture.png")
