import matplotlib.pyplot as plt

from heatcalc.core.models import BusbarSpec
from heatcalc.core.newton_calc import calc_tier_iec60890_coupled
from heatcalc.services.solver_report import dump_solver_state


# ----------------------------
# Minimal fake Tier for bench
# ----------------------------
class FakeRect:
    def __init__(self, width_mm, height_mm):
        self._w = width_mm
        self._h = height_mm

    def width(self): return self._w
    def height(self): return self._h


class FakeTier:
    def __init__(self, width_mm, height_mm, depth_mm, base_heat_w):
        self._rect = FakeRect(width_mm, height_mm)
        self.depth_mm = depth_mm

        self.component_entries = []
        self.cables = []
        self.is_ventilated = False
        self.vent_rows = 0
        self.vent_cols = 0

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
        class _Rect:
            def __init__(self, w, h):
                self._w = w
                self._h = h
            def left(self): return 0.0
            def right(self): return self._w
            def top(self): return 0.0
            def bottom(self): return self._h
        return _Rect(self._rect.width(), self._rect.height())


# ----------------------------
# Single test case
# ----------------------------
case = dict(name="Case", ambient=55.0, I=640.0, w=60.0, t=10.0, L=1.0)

tier = FakeTier(width_mm=500, height_mm=500, depth_mm=250, base_heat_w=0.0)

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
    BusbarSpec(name="Phase B", I_total_A=case["I"], bars_in_parallel=1,
              width_mm=case["w"], thickness_mm=case["t"], L_char_mm=case["w"],
              length_m=case["L"], face_to_face_dim="thickness",
              eps_bus=0.10, eps_env=0.90, convection_mode="horizontal",
              v_mps=0.0, S_ac=1.0, use_air_temp="top"),
    BusbarSpec(name="Phase C", I_total_A=case["I"], bars_in_parallel=1,
              width_mm=case["w"], thickness_mm=case["t"], L_char_mm=case["w"],
              length_m=case["L"], face_to_face_dim="thickness",
              eps_bus=0.10, eps_env=0.90, convection_mode="horizontal",
              v_mps=0.0, S_ac=1.0, use_air_temp="top"),
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
    debug=True,   # store trace in result["busbars"][i]["trace"]
)

dump_solver_state(result, show_trace=False)

# Simple convergence plot (coupling history)
hist = result["coupling"]["history"]

iters = [h["iteration"] for h in hist]
T_air = [h["T_top"] for h in hist]
P_guess = [h["P_guess"] for h in hist]
P_calc = [h["P_calc"] for h in hist]
residual = [h["residual"] for h in hist]

plt.figure(figsize=(8, 5))
plt.plot(iters, T_air, marker="o", label="T_air_top (C)")
plt.xlabel("Iteration")
plt.ylabel("Temperature (C)")
plt.grid(True)
plt.legend()
plt.title("Coupling Convergence (Air Temp)")
plt.tight_layout()
plt.show()