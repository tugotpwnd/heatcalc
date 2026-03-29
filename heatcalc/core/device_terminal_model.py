"""
Device Terminal Temperature Model
--------------------------------

This module provides a first-order thermal model for estimating the
LOAD-SIDE terminal temperature of a circuit breaker (CB) connected to a busbar.

The model is intentionally simple and designed for integration into
IEC 61439-style assembly thermal assessments.

----------------------------------------------------------------------
MODEL OVERVIEW
----------------------------------------------------------------------

We model the terminal temperature as:

    T_line = T_bus + ΔT_interface

    P_device = I^2 * R_pole

    ΔT_device = k * P_device

    T_load = T_line + ΔT_device

Where:
    - T_bus        : local busbar temperature (°C)
    - ΔT_interface : small interface rise between bus and CB terminal
    - R_pole       : electrical resistance of CB pole (Ω)
    - I            : current through the device (A)
    - P_device     : internal CB heat generation (W)
    - k            : effective thermal resistance (°C/W)
    - T_load       : load-side terminal temperature (°C)

----------------------------------------------------------------------
JUSTIFICATION OF MODEL
----------------------------------------------------------------------

1) Electrical Heating (Physics-based)
------------------------------------
The heat generated inside the breaker is:

    P = I^2 * R

This is standard Joule heating and consistent with:
    - IEC thermal modelling approaches
    - busbar thermal modelling already implemented

2) Temperature Rise Relationship
--------------------------------
Thermal systems obey:

    ΔT = P * R_th

Where R_th (°C/W) is thermal resistance.

In this model:
    k ≡ R_th (effective terminal thermal resistance)

So:

    ΔT_device = k * P_device

3) Origin of k (IMPORTANT)
--------------------------
The value of k is NOT derived analytically.

It is derived implicitly from:
    - manufacturer power loss data (W)
    - tested terminal temperature rise (°C)

i.e.:

    k = ΔT / P

From empirical data:

Typical MCCB values:
    - Terminal rise: ~50–60°C (tested, IEC/UL aligned)
    - Power loss:   ~10–80 W depending on size

This yields:

    k ≈ 0.5 → 3 °C/W

4) Why k = 2 is used here
-------------------------
We select:

    k = 2 °C/W

because:

    - It sits in the UPPER-MID range of real MCCB values
    - It represents smaller / less thermally efficient devices
    - It produces conservative (higher) terminal temperatures
    - It avoids under-predicting thermal risk

This is therefore a **conservative engineering assumption**

NOTES:
    - Large MCCBs → k ~0.5–1 (better cooling)
    - Small MCCBs → k ~2–4 (worse cooling)

So k = 2:
    ✔ conservative for medium/large devices
    ✔ realistic for small/compact devices
    ✔ safe default when no manufacturer data is available

5) Why line-side is tied to bus
-------------------------------
The line-side terminal is mechanically bolted to the busbar.

Therefore:

    T_line ≈ T_bus + small interface rise

We assume:

    ΔT_interface ≈ 2°C

This reflects:
    - good metallic contact
    - negligible thermal resistance compared to device internals

----------------------------------------------------------------------
LIMITATIONS
----------------------------------------------------------------------

- Does NOT capture:
    - detailed internal CB geometry
    - airflow variations inside enclosure
    - asymmetry between line/load terminals
    - terminal lug/cable heat effects

- This is a FIRST-ORDER ENGINEERING MODEL

- For compliance-critical work:
    → manufacturer data should override this model


"""

from dataclasses import dataclass


@dataclass
class CBTerminalResult:
    T_line_C: float
    T_load_C: float
    P_device_W: float
    delta_T_device_C: float


def estimate_cb_load_terminal_temp(
    T_bus_C: float,
    I_A: float,
    R_pole_ohm: float,
    k_C_per_W: float = 2.0,
    delta_T_interface_C: float = 2.0,
) -> CBTerminalResult:
    """
    Estimate circuit breaker terminal temperatures.

    Parameters
    ----------
    T_bus_C : float
        Local busbar temperature (°C)

    I_A : float
        Current through the circuit breaker (A)

    R_pole_ohm : float
        Electrical resistance of breaker pole (Ω)

    k_C_per_W : float, optional
        Effective thermal resistance (°C/W)
        Default = 2.0 (conservative MCCB assumption)

    delta_T_interface_C : float, optional
        Temperature rise between bus and line terminal (°C)
        Default = 2°C (good bolted joint)

    Returns
    -------
    CBTerminalResult
        Structured result containing:
            - line-side temperature
            - load-side temperature
            - device power loss
            - device temperature rise
    """

    # --------------------------------------------------------------
    # Step 1: Line-side terminal temperature
    # --------------------------------------------------------------
    # Strongly coupled to bus via metallic connection
    T_line = T_bus_C + delta_T_interface_C

    # --------------------------------------------------------------
    # Step 2: Electrical heating inside breaker
    # --------------------------------------------------------------
    # Joule heating (fundamental physics)
    P_device = I_A**2 * R_pole_ohm

    # --------------------------------------------------------------
    # Step 3: Thermal rise due to internal losses
    # --------------------------------------------------------------
    delta_T_device = k_C_per_W * P_device

    # --------------------------------------------------------------
    # Step 4: Load-side terminal temperature
    # --------------------------------------------------------------
    T_load = T_line + delta_T_device

    return CBTerminalResult(
        T_line_C=T_line,
        T_load_C=T_load,
        P_device_W=P_device,
        delta_T_device_C=delta_T_device,
    )