import matplotlib.pyplot as plt
import numpy as np

from heatcalc.core.models import BusbarSpec, BusbarJointSpec
from heatcalc.core.busbar_geometry import build_busbar_segments
from heatcalc.core.busbar_solver import solve_busbar_temperature


# -------------------------------------------------
# Test Busbar
# -------------------------------------------------

bus = BusbarSpec(
    name="Test Bus",
    I_total_A=2000.0,
    bars_in_parallel=1,
    width_mm=60.0,
    thickness_mm=10.0,
    length_m=2.0,
    L_char_mm=60.0,

    face_to_face_dim="thickness",

    eps_bus=0.10,
    eps_env=0.90,

    convection_mode="horizontal",
    v_mps=0.0,

    segments_per_m=40,

    joints=[
        BusbarJointSpec(
            x_m=0.5,
            overlap_m=0.05,
            bolt_count=4,
            R_contact_20_uohm=4.0
        )
    ]
)


ambient_C = 40.0


# -------------------------------------------------
# Solve
# -------------------------------------------------

result = solve_busbar_temperature(
    bus=bus,
    air_temp_C=ambient_C
)


# -------------------------------------------------
# Extract segment geometry
# -------------------------------------------------

segments = build_busbar_segments(bus)

print("Segments:")
for seg in segments:
    print(
        f"x={seg.start_m:.3f} m  "
        f"L={seg.length_m:.3f} m  "
        f"R_extra_per_m={seg.extra_R20_ohm_per_m:.6e}"
    )


segment_centres = []
segment_lengths = []

x = 0.0
for seg in segments:
    segment_centres.append(x + seg.length_m / 2)
    segment_lengths.append(seg.length_m)
    x += seg.length_m


# -------------------------------------------------
# Extract temperatures
# -------------------------------------------------

# temperature vector from solver
T_vec = result.trace[-len(segments):] if result.trace else None

# fallback (if trace disabled)
if T_vec is None:
    raise RuntimeError("Trace not enabled – cannot plot profile.")


# -------------------------------------------------
# Temperature rise
# -------------------------------------------------

temps = result.T_profile
T_max = max(temps)
rise = T_max - ambient_C

print("=================================================")
print("Busbar Temperature Result")
print("=================================================")

print(f"Ambient temperature      : {ambient_C:.1f} °C")
print(f"Maximum busbar temp      : {T_max:.2f} °C")
print(f"Temperature rise         : {rise:.2f} K")
print(f"Joint resistance         : {bus.joints[0].R_contact_20_uohm} µΩ")

print("=================================================")


# -------------------------------------------------
# Plot temperature distribution
# -------------------------------------------------

plt.figure(figsize=(9,5))

plt.plot(segment_centres, temps, marker="o")

plt.axvline(0.5, linestyle="--", color="red", label="Joint location")

plt.xlabel("Busbar Position (m)")
plt.ylabel("Temperature (°C)")
plt.title("Busbar Temperature Distribution with Joint Hotspot")

plt.grid(True)
plt.legend()

plt.tight_layout()
plt.show()