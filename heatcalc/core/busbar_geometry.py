from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


FaceToFaceDim = Literal["width", "thickness"]


@dataclass(frozen=True)
class BusbarGeometry:
    """
    Pure geometry / layout layer for a single bar (plus arrangement context).
    All units are SI (metres, m^2, etc).

    Notes
    -----
    - width_m, thickness_m refer to ONE bar cross-section.
    - bars_in_parallel affects current sharing AND (in this simplified model)
      the effective radiating area due to mutual shielding.
    """

    name: str

    # Cross-section for ONE bar
    width_m: float
    thickness_m: float

    # Characteristic length used in convection correlation (IEC-style)
    L_char_m: float

    # Electrical length that dissipates power
    length_m: float

    # Arrangement
    bars_in_parallel: int = 1
    face_to_face_dim: FaceToFaceDim = "thickness"

    # Orientation / convection mode (vertical vs horizontal plate correlations)
    convection_mode: Literal["vertical", "horizontal"] = "vertical"


def from_busbarspec_mm(spec) -> BusbarGeometry:
    """
    Convenience: build SI geometry from your existing BusbarSpec which is mm-based.
    Keeps your UI/data model unchanged, while solver uses SI internally.
    """
    return BusbarGeometry(
        name=spec.name,
        width_m=spec.width_mm / 1000.0,
        thickness_m=spec.thickness_mm / 1000.0,
        L_char_m=spec.L_char_mm / 1000.0,
        length_m=spec.length_m,
        bars_in_parallel=int(spec.bars_in_parallel),
        face_to_face_dim=spec.face_to_face_dim,
        convection_mode=spec.convection_mode,
    )


def surface_area_per_m(width_m: float, thickness_m: float) -> float:
    """
    External surface area per metre length for a rectangular bar (ignores ends).
    A_s = 2*(w + t) [m^2 per m]
    """
    return 2.0 * (width_m + thickness_m)


def effective_radiating_area_per_m(
    width_m: float,
    thickness_m: float,
    bars_in_parallel: int,
    face_to_face_dim: FaceToFaceDim,
) -> tuple[float, float, float]:
    """
    Returns (As_raw, As_eff, blockage_fraction).

    This is a simplified "average shielding" model:
    - As_raw is the full external surface area.
    - As_eff reduces radiation area when multiple bars are face-to-face.

    Important:
    - Convection area is NOT reduced here (your previous model used full area for convection),
      only radiation is reduced (consistent with your earlier intent).
    """
    As_raw = surface_area_per_m(width_m, thickness_m)

    if bars_in_parallel <= 1:
        return As_raw, As_raw, 0.0

    # The "blocked face dimension" is the dimension forming the face-to-face spacing
    d = width_m if face_to_face_dim == "width" else thickness_m

    # Your previous model: A_blocked_avg = 2*d*(1 - 1/N)
    A_blocked_avg = 2.0 * d * (1.0 - 1.0 / float(bars_in_parallel))

    As_eff = max(As_raw - A_blocked_avg, 0.0)

    blockage = 0.0
    if As_raw > 0:
        blockage = max(0.0, min(1.0, 1.0 - (As_eff / As_raw)))

    return As_raw, As_eff, blockage