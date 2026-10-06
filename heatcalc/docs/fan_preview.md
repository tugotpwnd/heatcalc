# Thermal preview and fan selection

Select a tier, open the **Preview** tab and choose **Solve network**. The overview
shows internal mid/top air temperature, tier limit and margin, the component with
the lowest temperature rating, the largest component heat subtotal (quantity
included), and component/cable/busbar heat totals. The capacity table shows amps
per device at the solved top temperature and at the tier limit for comparison.

Enter an optional fan reference and its delivered airflow in m³/h, then choose
**Save fan & solve network**. This saves the fan on that tier and repeats the
coupled enclosure/busbar solve. Zero airflow restores natural cooling. Old project
files load with no selected fan. The PDF uses the same solved temperatures and
includes the selected fan alongside component deratings.

The plot button opens an interactive fan-sizing dialog. Edit delivered airflow,
drag the slider, or click either plot to set a trial operating point. After a short
pause the coupled network recalculates, including temperature-dependent busbar
losses. Markers and annotations show top/mid temperature and each included device's
available current at that airflow. The curve sweep uses the trial operating point's
heat load. Only devices with a rated current and a usable derating expression are
plotted; missing, blank, invalid, or non-finite expressions are excluded.

Trial points do not modify the saved fan or main preview. **Use this fan** saves
the current airflow and optional reference to the tier and updates the main
network result. Closing the dialog leaves an unsaved trial unapplied. Plot range
changes preserve the operating point. The preview clears results when inputs
change and identifies unconverged results as provisional; a failed trial cannot
be exported.

**Export one-page PDF** produces a Maxwell-styled sheet with the project,
fan/filter reference, airflow, heat load, top/mid temperatures, limits,
annotated plots and the device operating-point schedule. The plots use Arial,
and the schedule shares the main report's table styling. It has no cover or
contents page. Normal schedules use A4; larger schedules use a larger sheet to
keep every device legible on one page. **Export plot image** saves the annotated
chart as PNG; the chart toolbar also provides zoom, pan and save controls.

## Calculation assumptions

In **Project Info**, enable **Use manufacturer derating curves instead of the
80% Ith limit** to use each eligible device's manufacturer curve without the
additional 80% cap. This project-wide setting is saved in project JSON and is off
by default, including for older projects. Devices without a usable curve retain
the 80% limit. Outputs never exceed rated current, and device temperature limits
still apply. Previews, fan plots and both PDF reports follow the setting; when
used, a note below the device table records the manufacturer-curve basis.

The new operating-point estimate inverts the application's existing Annex K
minimum-airflow balance:

    P = (ΔT / (c × k))^(1/x) + 1160 × altitude_factor × airflow_m3h / 3600 × ΔT

The sealed-enclosure coefficients and altitude factor come from the existing
calculator. No second natural-ventilation contribution is added. Mid-height rise
uses ΔT/c. Ambient plus the existing solar allowance is retained as the temperature
floor. Airflow is delivered flow through the installed enclosure/filter, rather
than a free-air fan rating. See also the manufacturer's explanation of
[airflow and enclosure temperature difference](https://www.rittal.de/downloads/eBook/TSH/EN/Climate_control/pubData/SEO/Page_6.html).

This is a steady-state heat-balance estimate, not a validated forced-flow spatial
temperature model. Applying a very small positive flow switches from the installed
natural-cooling model to the sealed fan model; the curve therefore marks the
natural-cooling point separately. Partition effects are handled as in the existing
Annex K sizing routine. Fans reduce temperature rise but do not eliminate heat
generation or cool below the model's external-temperature floor.

Current capacity uses `evaluate_derating`, including the 80% rated-current
ceiling unless the manufacturer-curve project setting is enabled, and the 80%
fallback for absent/invalid curves. Temperature-rating exceedances
are flagged separately; a current value alone is not a compliance verdict. The PDF
previously evaluated at the tier limit; it now evaluates at solved top temperature.

## Verification

From the package directory, with the parent directory on `PYTHONPATH` and the
application dependencies installed:

    python -m unittest discover -s tests -v

Tests cover the heat balance, minimum and oversized airflow, altitude/solar effects,
external-temperature limits, zero heat, input validation, persistence, stale and
failed results, real busbar coupling, repeat solves, PDF data, scroll sizing and the
chart dialog.
