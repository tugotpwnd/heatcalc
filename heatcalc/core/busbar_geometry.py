from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional
import numpy as np
from heatcalc.core.models import BusbarSpec

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


from dataclasses import dataclass
from typing import List

@dataclass
class BusbarSegment:
    start_m: float
    length_m: float
    width_m: float
    thickness_m: float
    extra_R20_ohm_per_m: float = 0.0


def build_busbar_segments(bus: BusbarSpec, breakpoints: List[float] | None = None):

    segments: List[BusbarSegment] = []

    if breakpoints is None:
        breakpoints = []

    # include branch points
    for br in getattr(bus, "branches", []) or []:
        breakpoints.append(br.x_m)

    # include joints
    for j in getattr(bus, "joints", []) or []:
        breakpoints.append(j.x_m)

    breakpoints = sorted(set(breakpoints))
    # --------------------------------------------------
    # Base resolution
    # --------------------------------------------------

    dx_nom = 1.0 / bus.segments_per_m

    # --------------------------------------------------
    # Build boundary list
    # --------------------------------------------------

    boundaries = {0.0, bus.length_m}

    for joint in bus.joints:

        x0 = joint.x_m - joint.overlap_m / 2
        x1 = joint.x_m + joint.overlap_m / 2

        boundaries.add(max(0.0, x0))
        boundaries.add(min(bus.length_m, x1))

    # add nominal grid
    x = 0.0
    while x < bus.length_m:
        boundaries.add(round(x, 9))
        x += dx_nom

    boundaries = sorted(boundaries)

    # --------------------------------------------------
    # Create segments
    # --------------------------------------------------

    for i in range(len(boundaries) - 1):

        x0 = boundaries[i]
        x1 = boundaries[i + 1]

        length = x1 - x0

        if length <= 1e-9:
            continue

        segments.append(
            BusbarSegment(
                start_m=x0,
                length_m=length,
                width_m=bus.width_m,
                thickness_m=bus.thickness_m,
            )
        )

    # --------------------------------------------------
    # Apply joint resistance
    # --------------------------------------------------

    for joint in bus.joints:

        x0 = joint.x_m - joint.overlap_m / 2
        x1 = joint.x_m + joint.overlap_m / 2

        x0 = max(0.0, x0)
        x1 = min(bus.length_m, x1)

        overlap_segments = []

        for seg in segments:

            seg_mid = seg.start_m + seg.length_m / 2

            if x0 <= seg_mid <= x1:
                overlap_segments.append(seg)

        if not overlap_segments:
            continue

        # convert micro-ohm to ohm
        R_contact = joint.R_contact_20_uohm * 1e-6

        # distribute resistance across overlap length
        L_cov = sum(seg.length_m for seg in overlap_segments)

        R_per_m = R_contact / L_cov

        for seg in overlap_segments:
            seg.extra_R20_ohm_per_m += R_per_m

    return segments