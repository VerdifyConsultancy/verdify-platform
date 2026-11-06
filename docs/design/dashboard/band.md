# The climate band, and how Control → Band sets it

Issue #1334. Research and numbers: [`docs/research/w5/vpd-band.md`](../../research/w5/vpd-band.md) (section H and the
recommended design). Decisions: [decisions](decisions.md), the rows of 2026-11-05. The dashboard is plant-agnostic: the
band is a temperature target and a VPD target, never a crop.

## What the band is

The band is the range the controller holds the house in. It has six series: temperature low, target and high, and VPD
low, target and high. Each series is set at four points of the sun's day:

| anchor | solar phase | when |
|---|---|---|
| `sr` | 0 | sunrise |
| `sm` | 1 | solar noon |
| `ss` | 2 | sunset |
| `mid` | 3 | solar midnight (halfway from sunset to the next sunrise) |

That is 24 numbers, `band_<series>_<anchor>`, stored in `crop_band_anchors` (`crop_type = 'house'`).

## How the curve is drawn

`fn_solar_phase` maps the clock to a phase from 0 to 4 with NOAA sun times, so the day/night cycle follows the season
and has no fixed hours. Each series is then one smooth curve through its four anchors:

```
θ = π·phase/2
value = c0 + c1·cos θ + s1·sin θ + c2·cos 2θ
c0 = (sr + sm + ss + mid)/4   c1 = (sr − ss)/2   s1 = (sm − mid)/2   c2 = (sr − sm + ss − mid)/4
```

The same formula runs in three places, which agree within 0.014 °F and 0.001 kPa on the live device:

- the controller, `firmware/lib/greenhouse_solar.h` (`band_value_at_phase`), evaluated in `firmware/greenhouse/controls.yaml`;
- Python, `ingestor/solar.py` and `verdify_schemas/band_profile.py` (`harmonic`);
- SQL, `fn_crop_band_value` and `fn_band_setpoints`.

The curve passes through every anchor but is not bounded by them. When sunrise and sunset are not the midpoint of noon
and midnight, it overshoots: the anchors of 2026-11-05 make the temperature edges run 1.54 °F past their lowest and
highest anchors every day. When sunrise = sunset = (noon + midnight)/2, `c1 = c2 = 0` and the curve is a pure sinusoid
between the noon and midnight values, with no overshoot.

## What the controller does at each edge

- Above temperature high it cools (vent, fans). Below temperature low it heats: electric heat1 first, which is the
  primary heat; gas heat2 is the backup when heat1 cannot keep up.
- Below VPD low the air is too humid, and the controller dries it. Above VPD high it is too dry, and the controller
  humidifies (misters, fog).
- The hard guardrails are 40 °F and 100 °F (`safety_min`, `safety_max`). The controller's `validate_setpoints` keeps them
  at least 5 °F outside the band, so the editor keeps the band within 45–95 °F. VPD has rails at 0.3 and 3.0 kPa.
- The night VPD floor stays at least 0.74 kPa, so the house dries out overnight, which also helps against algae on the
  floor (Jason, #1365). The editor refuses a lower night floor.

## Who may change what

- **The band (the 24 anchors)** is set only by an operator: Control → Band, a single setting in Settings, or an operator
  agent through the operator API. Iris (the planner) may not write it: `set_plan` drops anchors, and API-23 refuses Iris.
- **Night dry-out bias** (`night_vpd_bias_kpa`, 0–0.25 kPa) is the planner's lever. The controller adds
  `bias·sin²(π(phase−2)/2)` to VPD low, target and high from sunset to sunrise, peaking at solar midnight. The editor
  shows it dotted ("As the controller runs it").
- **Zones** ride their own VPD target curves for the misters. They are not part of this editor yet.

## The editor (Control → Band)

The simple profile (research Option A):

| field | meaning |
|---|---|
| Temperature day target, night target | the `temp_target` noon and midnight anchors |
| Temperature width ± (one value, optionally a separate night value) | `temp_low = target − width`, `temp_high = target + width` |
| VPD day target, night target | the `vpd_target` noon and midnight anchors |
| VPD range below and above, day and night | `vpd_low = target − below`, `vpd_high = target + above` |

Changing any temperature field recompiles the six temperature anchors (noon and midnight from the fields, sunrise and
sunset at their midpoint, each on the push quantum of 0.1 °F); any VPD field does the same for the VPD anchors (0.01
kPa). Advanced shows the 24 raw anchors; uneven shoulders warn when the overshoot passes 0.5 °F or 0.03 kPa.

**Preview (API-22, `GET /v1/band/profile`).** Read-only. The server overlays the draft on the anchors as
`fn_crop_band_value` resolves them and evaluates both with the controller's curve at 10-min steps of the day's
`fn_solar_phase`, so the preview is the device's curve. It also returns:

- the curve with the night bias, as the controller runs it;
- an expected night VPD ribbon (p25–p75): indoor dewpoint fitted on the last 14 days' deep-night hours with no vent,
  fan, fog or mister running (`Td_in = a + b·T_in + c·T_out + e·Td_out`), applied to the draft temperature target and the
  forecast;
- the night heat estimate, 0.19 kW per °F of target above the forecast outdoor temperature;
- findings: errors (band order, guardrails, the night floor) and warnings (overshoot; a night floor that needs an
  indoor dewpoint below the driest tenth of the last 14 nights).

**Save (API-23, `PUT /v1/band/profile`).** One transaction: every anchor that changes by more than half its push quantum
is written as an API-10 setting write (its `desired_state` row with the manual flag and its `crop_band_anchors` rows),
under one `activity_log` row, `band.write`. Last write wins; the manual flag works as everywhere else. The
`desired_state` insert wakes the dispatcher, whose anchor sync (`ingestor/tasks/band_anchors.py`) pushes the changed
anchors. Nothing in the dashboard or the operator API talks to the controller.

**Live check (#1334 acceptance).** On dashboard.verdify.ai change the night temperature target, see the curve move,
Save, and within two dispatcher cycles see `band_temp_target_mid` confirmed in Settings and Band in force follow.
