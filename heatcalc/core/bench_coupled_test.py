import matplotlib.pyplot as plt

from heatcalc.core.models import Tier, BusbarSpec, Component
from heatcalc.ui.tier_item import ComponentEntry
from heatcalc.core.newton_calc import calc_tier_iec60890_coupled

# ============================================================
# CREATE SIMPLE TEST TIER
# ============================================================

class FakeRect:
    def __init__(self, width_mm, height_mm):
        self._w = width_mm
        self._h = height_mm

    def width(self):
        return self._w

    def height(self):
        return self._h


class FakeTier:
    def __init__(self, width_mm, height_mm, depth_mm, base_heat_w):
        self._rect = FakeRect(width_mm, height_mm)
        self.depth_mm = depth_mm

        # Mimic TierItem structure
        self.component_entries = []
        self.cables = []
        self.is_ventilated = False
        self.vent_rows = 0
        self.vent_cols = 0

        # Inject synthetic heat via component-style object
        class _Comp:
            def __init__(self, heat):
                self.heat_each_w = heat
                self.qty = 1
                self.max_temp_C = 90

        self.component_entries.append(_Comp(base_heat_w))

        self.use_auto_component_temp = False
        self.max_temp_C = 90

    @property
    def total_heat_w(self):
        return sum(c.heat_each_w * c.qty for c in self.component_entries)

    def effective_max_temp_C(self):
        return self.max_temp_C

    def shapeRect(self):
        """
        Mimic TierItem.shapeRect().
        Returns an object with left(), right(), top(), bottom()
        """

        class _Rect:
            def __init__(self, w, h):
                self._w = w
                self._h = h

            def left(self):
                return 0.0

            def right(self):
                return self._w

            def top(self):
                return 0.0

            def bottom(self):
                return self._h

        return _Rect(self._rect.width(), self._rect.height())


# ============================================================
# RUN COUPLED SOLVER
# ============================================================
import matplotlib.pyplot as plt

# ============================================================
# PARAMETRIC TEST CASES (EDIT THESE)
# ============================================================

cases = [
    dict(name="Case 1", ambient=55, I=278, w=20,  t=10,  L=1.0),
    dict(name="Case 2", ambient=55, I=372, w=30, t=10, L=1.0),
    dict(name="Case 3", ambient=55, I=465, w=40, t=10, L=1.0),
    dict(name="Case 4", ambient=55, I=640, w=60, t=10, L=1.0),
    dict(name="Case 5", ambient=55, I=806, w=80, t=10, L=1.0),
    # dict(name="Case 6", ambient=55, I=969, w=100, t=10, L=1.0),
    dict(name="Case 6", ambient=31.85, I=260, w=25, t=12, L=0.3),
]

# ============================================================
# RUN ALL CASES
# ============================================================

fig, axes = plt.subplots(3, 2, figsize=(14, 14))
axes = axes.flatten()

for idx, case in enumerate(cases):

    tier = FakeTier(
        width_mm=500,
        height_mm=500,
        depth_mm=250,
        base_heat_w=0,
    )

    # Create 9 identical busbars
    tier.busbars = [
        BusbarSpec(
            name="Phase A",
            I_total_A=case["I"],
            bars_in_parallel=1,
            width_mm=case["w"],
            thickness_mm=case["t"],
            L_char_mm=case["w"],
            length_m=case["L"],
            face_to_face_dim="thickness",
            eps_bus=0.10,
            eps_env=0.90,
            convection_mode="horizontal",
            v_mps=0.0,
            S_ac=1.0,
            use_air_temp="top",
        ),
        BusbarSpec(
            name="Phase B",
            I_total_A=case["I"],
            bars_in_parallel=1,
            width_mm=case["w"],
            thickness_mm=case["t"],
            L_char_mm=case["w"],
            length_m=case["L"],
            face_to_face_dim="thickness",
            eps_bus=0.10,
            eps_env=0.90,
            convection_mode="horizontal",
            v_mps=0.0,
            S_ac=1.0,
            use_air_temp="top",
        ),
        BusbarSpec(
            name="Phase C",
            I_total_A=case["I"],
            bars_in_parallel=1,
            width_mm=case["w"],
            thickness_mm=case["t"],
            L_char_mm=case["w"],
            length_m=case["L"],
            face_to_face_dim="thickness",
            eps_bus=0.10,
            eps_env=0.90,
            convection_mode="horizontal",
            v_mps=0.0,
            S_ac=1.0,
            use_air_temp="top",
        ),
    ]

    result = calc_tier_iec60890_coupled(
        tier=tier,
        tiers=[tier],
        wall_mounted=False,
        inlet_area_cm2=0.0,
        ambient_C=case["ambient"],
        altitude_m=0.0,
        ip_rating_n=5,
        solar_delta_K=0.0,
    )

    history = result["coupling"]["history"]

    iters = [h["iter"] for h in history]
    T_air = [h["T_air_C"] for h in history]
    final_air_temp = result["T_top"]

    final_bus_loss = sum(b["P_loss_W"] for b in result["busbars"])
    final_bus_temp = max(b["T_bus_C"] for b in result["busbars"])

    ax = axes[idx]

    # Plot air temperature
    ax.plot(iters, T_air, marker="o", label="Air Temp (°C)")

    # Plot ALL busbars
    num_busbars = len(history[0]["busbars"])

    for b_index in range(num_busbars):
        T_bus = [
            h["busbars"][b_index]["T_bus_C"]
            for h in history
        ]
        bus_name = history[0]["busbars"][b_index]["name"]
        ax.plot(iters, T_bus, marker="s", label=f"{bus_name} Temp (°C)")

    ax.set_xlabel("Iteration")
    ax.set_ylabel("Temperature (°C)")
    ax.grid(True)

    title = (
        f"{case['name']}\n"
        f"I={case['I']}A | {case['w']}×{case['t']} mm | L={case['L']} m\n"
        f"Final Air = {final_air_temp:.1f}°C | "
        f"Final Bus = {final_bus_temp:.1f}°C | "
        f"Bus Loss = {final_bus_loss:.1f} W"
    )

    ax.set_title(title)
    ax.legend(loc="best")

plt.tight_layout()
plt.show()