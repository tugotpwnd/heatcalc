# heatcalc/core/curvefit.py
from __future__ import annotations
import math
from typing import List, Tuple
from ..core.iec60890_geometry import (
    touching_sides,
    b_map_for_tier,
    dimensions_m,
    effective_area_and_fg,
)


# ---------- utilities ----------

# --- utilities (replace the old _interp / _lerp with this block) ---

from dataclasses import dataclass

@dataclass
class _SplineSeg:
    x0: float; x1: float
    y0: float; y1: float
    m0: float; m1: float  # endpoint slopes for Hermite

class _MonotoneSpline:
    """Fritsch–Carlson monotone cubic Hermite spline (no SciPy needed)."""
    def __init__(self, pts: list[tuple[float, float]]):
        pts = sorted((float(x), float(y)) for x, y in pts)
        self._segs: list[_SplineSeg] = []
        n = len(pts)
        if n < 2:
            return
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        # secant slopes
        dx = [xs[i+1]-xs[i] for i in range(n-1)]
        dy = [ys[i+1]-ys[i] for i in range(n-1)]
        m  = [dy[i]/dx[i] if dx[i] != 0 else 0.0 for i in range(n-1)]

        # endpoint derivatives (Fritsch–Carlson)
        d = [0.0]*n
        d[0] = m[0]
        d[-1] = m[-1]
        for i in range(1, n-1):
            if m[i-1]*m[i] <= 0:
                d[i] = 0.0
            else:
                w1 = 2*dx[i] + dx[i-1]
                w2 = dx[i] + 2*dx[i-1]
                d[i] = (w1 + w2) / (w1/m[i-1] + w2/m[i])

        # build segments
        for i in range(n-1):
            self._segs.append(_SplineSeg(xs[i], xs[i+1], ys[i], ys[i+1], d[i], d[i+1]))

    def __call__(self, x: float) -> float:
        if not self._segs:
            return 0.0
        # clamp
        if x <= self._segs[0].x0:
            return self._segs[0].y0
        if x >= self._segs[-1].x1:
            return self._segs[-1].y1
        # locate segment (linear scan is fine for our sizes; switch to bisect if you like)
        for s in self._segs:
            if s.x0 <= x <= s.x1:
                t = (x - s.x0) / (s.x1 - s.x0)
                # cubic Hermite basis
                h00 = (2*t**3 - 3*t**2 + 1)
                h10 = (t**3 - 2*t**2 + t)
                h01 = (-2*t**3 + 3*t**2)
                h11 = (t**3 - t**2)
                return (h00*s.y0 + h10*(s.x1 - s.x0)*s.m0 +
                        h01*s.y1 + h11*(s.x1 - s.x0)*s.m1)
        # fallback
        return self._segs[-1].y1


# ---------- domains used by the UI ----------

AE_RANGE_LARGE = [0.5 + i * 0.05 for i in range(0, 231)]   # 0.5 .. 12.0 (m²) for Fig.3
OPENING_AREA_RANGE = list(range(0, 701, 10))               # 0 .. 700 cm² for Fig.5/6
F_RANGE = [1.0 + i * 0.1 for i in range(0, 101)]           # 1.0 .. 11.0 for Fig.4
G_RANGE = [i * 0.02 for i in range(0, 131)]                # 0.0 .. 2.6 for Fig.8
AE_RANGE_SMALL = [0.05 + i * 0.01 for i in range(0, 126)]  # 0.05 .. 1.30 for Fig.7

# ---------- Fig.3: k vs Ae (no ventilation, Ae > ~0.5 m²) ----------

# Fig.3
_FIG3_PTS = [
    (0.5, 0.98),(1.0, 0.60),(2.0, 0.35),(3.0, 0.25),(4.0, 0.18),(5.0, 0.16),
    (6.0, 0.14), (6.64, 0.135) ,(7.0, 0.13),(8.0, 0.12),(9.0, 0.11),(10.0, 0.10),(11.0, 0.09),(12.0, 0.08),
]
_FIG3_SPL = _MonotoneSpline(_FIG3_PTS)

def k_no_vents(ae_m2: float) -> float:
    return max(0.06, min(1.0, _FIG3_SPL(float(ae_m2))))


# ---------- Fig.4: c vs f (no ventilation), families 1..5 ----------

# Fig.4 base curve (curve 1), then offsets for 2..5
_FIG4_CURVE1_PTS = [
    (1.0, 1.230),(2.0, 1.275),(3.0, 1.325),(4.0, 1.375),
    (5.0, 1.415),(6.0, 1.450),(7.0, 1.480),(11.0, 1.575),
]
_FIG4_SPL = _MonotoneSpline(_FIG4_CURVE1_PTS)
_FIG4_OFFSETS = {1: 0.00, 2: -0.015, 3: -0.03, 4: -0.055, 5: -0.1}

def c_no_vents(curve_no: int, f: float) -> float:
    return _FIG4_SPL(float(f)) + _FIG4_OFFSETS.get(int(curve_no), 0.0)

# ---- Fig.5: k for ventilated enclosures (k vs inlet-opening area) ----
import bisect
import numpy as np

AE_FAMILIES_FIG5 = (1, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10, 12, 14)
OPENING_AREA_RANGE = np.linspace(50.0, 700.0, 200)  # cm²

# Base Ae=1 anchors (your data)
_F5_S  = np.array([50.0, 200.0, 400.0, 700.0], dtype=float)
_F5_K1 = np.array([0.36,  0.2175, 0.1380, 0.0880], dtype=float)

# Adjacent-family spacing Δk(s): 0.02 @ 50, 0.005 @ 400 (smooth in-between)
_F5_SG = np.array([50.0, 400.0], dtype=float)
_F5_DK = np.array([0.02,  0.005], dtype=float)

# Monotone (PCHIP-like) slopes and evaluator (shape-preserving)
def _pchip_slopes(x, y):
    x = np.asarray(x); y = np.asarray(y)
    n = x.size
    m = np.zeros(n)
    if n < 2: return m
    dx = np.diff(x); dy = np.diff(y); s = dy / dx
    m[0] = s[0]; m[-1] = s[-1]
    for i in range(1, n-1):
        if s[i-1]*s[i] <= 0: m[i] = 0.0
        else:
            w1 = 2*dx[i] + dx[i-1]
            w2 = dx[i] + 2*dx[i-1]
            m[i] = (w1 + w2) / (w1/s[i-1] + w2/s[i])
    return m

def _pchip_eval(xk, yk, xq):
    xk = np.asarray(xk); yk = np.asarray(yk)
    mk = _pchip_slopes(xk, yk)

    def _eval_scalar(x):
        if x <= xk[0]:  return float(yk[0])
        if x >= xk[-1]: return float(yk[-1])
        j = bisect.bisect_right(xk, x) - 1
        h = xk[j+1] - xk[j]; t = (x - xk[j]) / h
        h00 = (2*t**3 - 3*t**2 + 1);  h10 = (t**3 - 2*t**2 + t)
        h01 = (-2*t**3 + 3*t**2);     h11 = (t**3 - t**2)
        return float(h00*yk[j] + h10*h*mk[j] + h01*yk[j+1] + h11*h*mk[j+1])

    if np.isscalar(xq): return _eval_scalar(float(xq))
    xq = np.asarray(xq, dtype=float)
    return np.vectorize(_eval_scalar)(xq)

# Smooth base for Ae=1 and smooth Δk(s)
def _fig5_base_k(s): return _pchip_eval(_F5_S,  _F5_K1, s)
def _fig5_gap(s):    return _pchip_eval(_F5_SG, _F5_DK, s)

def _fig5_family_index(ae: float) -> int:
    A = float(ae)
    return int(np.argmin([abs(A - f) for f in AE_FAMILIES_FIG5]))

# Analytic family curve: fit τ from two targets (s1=50, s2=400)
# k(s) = c + A * exp(-(s-50)/τ), with c fixed (~common asymptote)
_F5_C_ASYM = 0.065  # common tail near 700 cm²

def _fig5_family_params(ae: float):
    n = _fig5_family_index(ae)
    s1, s2 = 50.0, 400.0
    k50  = _fig5_base_k(s1) - n * _fig5_gap(s1)
    k400 = _fig5_base_k(s2) - n * _fig5_gap(s2)
    c = _F5_C_ASYM
    # Guard against numerical issues
    num = (k400 - c) / max(1e-9, (k50 - c))
    num = max(1e-6, min(0.999999, num))
    tau = -(s2 - s1) / math.log(num)
    A = (k50 - c)  # because at s=50, exp(0)=1
    return A, tau, c

def k_vents(ae: float, opening_area_cm2: float) -> float:
    """Figure 5 — ventilated enclosures: exponential families per Ae."""
    s = float(max(50.0, min(700.0, opening_area_cm2)))
    A, tau, c = _fig5_family_params(ae)
    k = c + A * math.exp(-(s - 50.0) / max(1e-6, tau))
    return max(0.06, min(0.38, k))


# ---------- Fig.6: c vs inlet opening area (with ventilation), families by f ----------

def c_vents(f: float, opening_area_cm2: float) -> float:
    """
    Curves start at different c(0) depending on f, then saturate upward.
    """
    f = max(1.5, min(10.0, float(f)))
    s = max(0.0, float(opening_area_cm2))
    c0 = 1.20 + 0.045 * (f - 1.5)     # ~1.20 .. ~1.62 at s=0
    c_inf = 1.85 + 0.030 * (f - 1.5)  # ~1.85 .. ~2.26 as s→∞
    tau = 80.0 + 10.0 * f
    return min(2.2, c0 + (c_inf - c0) * (1.0 - math.exp(-s / tau)))

# ---------- Fig.7: k vs Ae (no ventilation, Ae ≤ 1.25 m²) ----------

# Use a power-law fit passing through two representative points from the scan:
# (Ae=0.05 m² → k≈4.0), (Ae=1.30 m² → k≈0.55)
_A1, _K1 = 0.1, 3.5
_A2, _K2 = 1, 0.63
# Solve k = C * Ae^B
_B = (math.log(_K2) - math.log(_K1)) / (math.log(_A2) - math.log(_A1))
_C = _K1 / (_A1 ** _B)

def k_small_no_vents(ae_m2: float) -> float:
    ae = max(0.05, min(1.30, float(ae_m2)))
    return _C * (ae ** _B)

# ---------- Fig.8: c vs g (no ventilation, small Ae ≤ 1.25) ----------
# Fig.8
_FIG8_PTS = [(0.0,1.00),(0.5,1.1),(1.0,1.19),(1.5,1.23),(2.0,1.25),(2.5,1.255)]
_FIG8_SPL = _MonotoneSpline(_FIG8_PTS)

def c_small_no_vents(g: float) -> float:
    return _FIG8_SPL(float(g))
