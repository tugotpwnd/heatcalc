"""The same annotated chart is used in the dialog, image export and PDF."""
from __future__ import annotations

import textwrap
from matplotlib import colormaps
from matplotlib.figure import Figure

BLUE = "#215096"
GREEN = "#007F4D"


def draw_fan_sizing_figure(figure: Figure, data):
    figure.clear()
    temperatures, capacities = figure.subplots(2, 1, sharex=True)
    # Reserved annotation column prevents coincident device capacities from
    # placing their text on top of one another.
    figure.subplots_adjust(left=.09, right=.71, bottom=.1, top=.95, hspace=.3)
    r, flow = data.result, data.airflow_m3h
    temperatures.plot(data.flows_m3h, data.tops_C, color=BLUE, label="Top")
    temperatures.plot(data.flows_m3h, data.mids_C, color=GREEN, label="Mid")
    temperatures.scatter([0], [r["uncooled_T_top"]], color=BLUE, s=12)
    temperatures.axhline(r["limit_C"], color="#bc4242", linestyle="--", label="Tier limit")
    if r.get("airflow_m3h", 0) > 0:
        temperatures.axvline(r["airflow_m3h"], color="#888888", linestyle="--",
                             label="Minimum airflow")
    for key, label, color, y in (("T_top", "Top", BLUE, .92), ("T_mid", "Mid", GREEN, .62)):
        temperatures.scatter([flow], [r[key]], s=32, color=color, zorder=5)
        temperatures.annotate(
            f"{label} {r[key]:.1f} °C\n@ {flow:.1f} m³/h",
            xy=(flow, r[key]), xytext=(1.04, y), textcoords="axes fraction",
            fontsize=8, color=color, va="top", annotation_clip=False,
            arrowprops=dict(arrowstyle="-", color=color, alpha=.45),
        )
    temperatures.text(1.04, .23, f"Limit {r['limit_C']:.1f} °C\n"
                      f"Margin {r['limit_C'] - r['T_top']:+.1f} °C",
                      transform=temperatures.transAxes, fontsize=8, va="top")
    temperatures.set_ylabel("Internal air (°C)", fontsize=9)
    temperatures.legend(fontsize=7, loc="upper right")
    palette = colormaps["tab10"]
    count = len(data.devices)
    ordered_devices = sorted(enumerate(data.devices), key=lambda pair: -pair[1].current_A)
    for row, (i, device) in enumerate(ordered_devices):
        color = palette(i % 10)
        capacities.plot(data.flows_m3h, device.currents_A, color=color, label=device.label)
        capacities.scatter([flow], [device.current_A], color=color, s=26, zorder=5)
        name = textwrap.shorten(device.name, width=27, placeholder="...")
        text = (f"{device.label} {name}\n{device.current_A:.1f} A @ {flow:.1f} m³/h")
        capacities.annotate(
            text, xy=(flow, device.current_A), xytext=(1.04, 1 - (row + .1) / max(count, 1)),
            textcoords="axes fraction", fontsize=7, va="top", color=color,
            annotation_clip=False, parse_math=False,
            arrowprops=dict(arrowstyle="-", color=color, alpha=.35),
        )
    if not data.devices:
        capacities.text(.5, .5, "No devices with valid derating curves",
                        transform=capacities.transAxes, ha="center", fontsize=9)
    capacities.set_ylabel("Available current / device (A)", fontsize=9)
    capacities.set_xlabel("Delivered airflow (m³/h)", fontsize=9)
    for ax in (temperatures, capacities):
        ax.axvline(flow, color=BLUE, linestyle=":", linewidth=1)
        ax.set_xlim(0, data.maximum_m3h * 1.02)
        ax.grid(alpha=.18)
        ax.tick_params(labelsize=8)
    return temperatures, capacities
