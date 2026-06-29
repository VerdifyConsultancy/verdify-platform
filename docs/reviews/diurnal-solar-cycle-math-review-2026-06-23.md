# Diurnal solar-cycle math and nature-alignment review - 2026-06-23

## Scope

This is a math-based review of the diurnal cycle calculation used to drive the
greenhouse temperature/VPD band. It compares:

- Firmware solar math and band interpolation.
- Live DB solar math and graph/planner band surfaces.
- Measured outdoor solar irradiance, outdoor temperature, indoor temperature,
  indoor VPD, and actuator utilization.
- Whether current band anchors and solar offsets can be tuned to align better
  with the greenhouse's natural response.

Only read-only production DB queries and local source inspection were used. No
device, ArgoCD, DB, or tunable writes were performed.

## Short Opinion

The firmware-side solar math is good enough. The live DB solar math is not
seasonally correct: it is close in June, but its altitude helper hardcodes solar
noon at 13:00 local, which makes winter sunrise/noon/sunset roughly one hour
late versus the firmware/NOAA calculation. Fix that before making any seasonal
anchor claims from DB curves or planner analysis.

For summer operation, the current target curve is not wildly wrong. Its target
peak is about 3.25 hours after solar noon, while measured indoor temperature and
VPD peaks are about 2 hours after solar noon, vent runtime peaks about 3 hours
after solar noon, and outdoor air peaks about 4-4.5 hours after solar noon. The
curve is between the crop-air response and the outdoor-air lag.

My tuning opinion:

1. First fix DB solar phase to match firmware/NOAA year-round.
2. Then follow ADR-0004 and push `band_track_fraction -> 0` for a floating
   corridor trial. Most daytime temperature "miss" is created by the pinched
   tracking corridor, not by the served crop envelope.
3. Do not move the summer temp curve later. If anything, if target tracking is
   kept, move the curve peak about 45-75 minutes earlier. But I would not tune
   this until after the float trial because the full served temp band already
   contains the actual air 99% of the post-sync window.
4. VPD is the real remaining axis. Floating helps VPD only slightly; daytime VPD
   is still dry. Either allow a modestly wider high-light VPD corridor if the
   crop physiology permits it, or improve wetting/vent-humidification response
   around solar noon +1h to +4h. Do not solve that by shifting the whole curve
   later.

## Current Math Surfaces

### Firmware

`firmware/lib/greenhouse_solar.h` computes sunrise, solar noon, and sunset on
chip using a NOAA-style solar-position approximation with:

- Longmont site constants: latitude `40.167`, longitude `-105.102`.
- Current UTC offset, including DST, derived from local vs UTC time.
- Sunrise/sunset zenith `90.833` degrees.

The firmware then maps local minute of day to a continuous solar phase:

```text
phase 0 = sunrise
phase 1 = solar noon
phase 2 = sunset
phase 3 = solar midnight
```

The phase function is C1-smooth piecewise cubic Hermite. The band value is a
four-anchor harmonic interpolation:

```text
theta = pi * phase / 2
c0 = (sr + sm + ss + mid) / 4
c1 = (sr - ss) / 2
s1 = (sm - mid) / 2
c2 = (sr - sm + ss - mid) / 4
value = c0 + c1*cos(theta) + s1*sin(theta) + c2*cos(2*theta)
```

This passes exactly through the four anchors but may peak between anchors.

### Live DB

The live DB `fn_crop_band_value()` uses the same harmonic interpolation shape.
That part is aligned.

The live DB `fn_solar_phase()` is not using the same event calculation as the
firmware. It gets sunrise/sunset from `fn_solar_sunrise_hour()` and
`fn_solar_sunset_hour()`, which binary-search `fn_solar_altitude()`. The problem
is `fn_solar_altitude()` uses:

```text
hour_angle = 15 * (local_hour - 13.0)
```

That hardcodes solar noon at 13:00 local all year. Near June in MDT this is
accidentally close. In winter MST it is about an hour wrong.

Representative event comparison:

| Date | Firmware/Python NOAA sunrise | Firmware/Python NOAA noon | Firmware/Python NOAA sunset | Live DB sunrise | Live DB noon midpoint | Live DB sunset |
|---|---:|---:|---:|---:|---:|---:|
| 2026-03-20 | 07:05 | 13:08 | 19:12 | 07:04 | 13:00 | 18:57 |
| 2026-06-21 | 05:31 | 13:02 | 20:33 | 05:35 | 13:00 | 20:26 |
| 2026-09-22 | 06:47 | 12:53 | 18:59 | 07:01 | 13:00 | 19:00 |
| 2026-12-21 | 07:19 | 11:58 | 16:38 | 08:26 | 13:00 | 17:35 |

Current June live telemetry hides most of this:

| Local date | Device sunrise | Device noon | Device sunset | DB sunrise | DB noon midpoint | DB sunset |
|---|---:|---:|---:|---:|---:|---:|
| 2026-06-15 | 05:30 | 13:00 | 20:31 | 05:35 | 13:00 | 20:26 |
| 2026-06-22 | 05:31 | 13:02 | 20:33 | 05:35 | 13:00 | 20:26 |

Near solstice that is only a 4-7 minute sunrise/sunset difference, but it is not
a year-round guarantee.

After the June 18 band sync, device-vs-DB target divergence was small enough for
this review:

| Window | Rows | Avg temp target diff | P95 temp target diff | Avg VPD target diff | P95 VPD target diff |
|---|---:|---:|---:|---:|---:|
| 2026-06-18 17:17 UTC onward | 4,539 | 0.097 F | 0.297 F | 0.003 kPa | 0.005 kPa |

So for the current summer data, tuning conclusions are not dominated by device/DB
target drift. The seasonal DB math still needs correction before fall/winter.

## Current Band Shape

Current house target anchors:

| Series | Sunrise | Solar noon | Sunset | Solar midnight |
|---|---:|---:|---:|---:|
| `temp_target` | 67.50 F | 78.79 F | 77.00 F | 65.71 F |
| `vpd_target` | 0.935 kPa | 1.042 kPa | 1.025 kPa | 0.918 kPa |

Because the curve is harmonic, the target peak is not exactly at the `sm` anchor.
Sampling the live DB curve at 5-minute resolution gives:

| Date | Temp target peak | Peak phase | Offset from solar noon | Peak temp target | VPD target peak | VPD peak target |
|---|---:|---:|---:|---:|---:|---:|
| 2026-06-15..21 | 16:15 MDT | 1.403 | +194.5 min | 80.33 F | 16:15 MDT | 1.057 kPa |

That late peak is mainly caused by the sunset anchor staying high relative to the
solar-noon anchor. Offline sensitivity using the current temp target anchors:

| If `temp_target_ss` were | Peak phase | Approx offset after solar noon | Peak target |
|---:|---:|---:|---:|
| 73 F | 1.164 | +74 min | 79.14 F |
| 74 F | 1.212 | +96 min | 79.33 F |
| 75 F | 1.268 | +121 min | 79.58 F |
| 76 F | 1.332 | +149 min | 79.91 F |
| 77 F current | 1.400 | +180-195 min | 80.33 F |
| 78 F | 1.468 | +211 min | 80.85 F |

The same timing can also be pulled earlier by raising the solar-noon anchor, but
that raises the peak target. Lowering the sunset anchor shifts earlier while
also asking for faster evening cool-down.

## Measured Natural Timing

For complete post-solar-telemetry days `2026-06-15` through `2026-06-21`:

| Cohort | Days | Median measured solar peak | Median solar centroid | Median outdoor temp peak | Median indoor temp peak | Median VPD peak |
|---|---:|---:|---:|---:|---:|---:|
| All days | 7 | +19 min | +9 min | +271 min | +160 min | +119 min |
| Clear-ish, max solar > 1000 W/m2 | 6 | +16.5 min | +8.1 min | +255 min | +121 min | +112.5 min |

Interpretation:

- Irradiance itself peaks very near solar noon, as expected; individual max rows
  are cloud-sensitive, so centroid is the better daily metric.
- Outdoor air temperature peaks much later, about solar noon +4h to +4.5h.
- Indoor temp and VPD peak earlier than outdoor air, around solar noon +2h.
- The current target peak at solar noon +3.25h sits between the greenhouse air
  response and outdoor air response.

Lag correlation using 15-minute buckets after the band rollout:

| Driver leads response by | Solar to indoor temp corr | Solar to VPD corr | Solar to vent duty corr | Solar to wet duty corr | Solar to outdoor temp corr |
|---:|---:|---:|---:|---:|---:|
| 0 min | 0.655 | 0.715 | 0.217 | 0.637 | 0.522 |
| 90 min | 0.832 | 0.832 | 0.365 | 0.755 | 0.766 |
| 105 min | 0.851 | 0.835 peak | 0.376 | 0.768 peak | 0.795 |
| 135 min | 0.877 | 0.832 | 0.384 | 0.765 | 0.839 |
| 180 min | 0.890 peak | 0.802 | 0.386 peak | 0.745 | 0.875 |
| 210 min | 0.879 | 0.756 | 0.359 | 0.709 | 0.882 peak |

This says the greenhouse "feels" solar load quickly in VPD/wetting demand
around 90-135 minutes, while bulk air temperature and vent demand respond around
180 minutes. Outdoor air is later, around 210 minutes.

## Runtime and Error by Solar Offset

Using post-rollout rows through the last complete local day, grouped by hours
from device solar noon:

| Offset from solar noon | Avg temp minus target | Avg VPD minus target | Avg solar | Vent duty | Wet duty |
|---:|---:|---:|---:|---:|---:|
| -2h | +1.07 F | +0.060 kPa | 841 W/m2 | 8.1% | 4.7% |
| -1h | +2.52 F | +0.112 kPa | 910 W/m2 | 21.4% | 18.4% |
| 0h | +3.01 F | +0.211 kPa | 950 W/m2 | 47.1% | 37.6% |
| +1h | +3.31 F | +0.232 kPa | 813 W/m2 | 53.5% | 44.8% |
| +2h | +3.40 F | +0.270 kPa | 762 W/m2 | 70.0% | 55.4% |
| +3h | +3.11 F | +0.209 kPa | 597 W/m2 | 76.3% | 40.5% |
| +4h | +2.84 F | +0.163 kPa | 445 W/m2 | 50.4% | 34.2% |
| +5h | +2.59 F | +0.112 kPa | 280 W/m2 | 35.2% | 18.9% |

Vent runtime peaks around solar noon +3h. Wet runtime peaks around +2h. This
matches the lag correlation and confirms the controller/equipment can feel the
real load peak.

The important ADR-0004 comparison is pinched-vs-served corridor:

| Slice | Rows | Temp in pinched corridor | Temp in served band | VPD in pinched corridor | VPD in served band | Both in pinched | Both in served |
|---|---:|---:|---:|---:|---:|---:|---:|
| Overall, post sync | 3,734 | 89.7% | 99.0% | 56.2% | 57.1% | 55.1% | 57.1% |
| Solar noon to +5h | 1,256 | 75.1% | 97.1% | 31.8% | 34.5% | 29.9% | 34.5% |

Temperature is mostly fine if the system floats in the served corridor. The
current pinched tracking regime creates most of the apparent daytime temperature
miss. VPD remains weak even under the served corridor, so VPD needs separate
attention.

## Tuning Recommendations

### 1. Fix DB solar math before seasonal tuning

Replace the live DB `fn_solar_altitude`/sunrise/sunset/phase chain with the same
NOAA event math used by firmware and `ingestor/solar.py`, or make DB phase use
device-published solar event times where appropriate.

Acceptance checks should include representative dates:

- March equinox.
- June solstice.
- September equinox.
- December solstice.

The DB and firmware/Python mirror should agree within the existing plus/minus
5-minute contract. This is the highest-confidence finding in the review.

### 2. Float temperature before editing temp anchors

Current `band_track_fraction` is `0.25`, despite ADR-0004 accepting floating
corridor control. A no-OTA tunable trial to `0` is the cleanest next experiment.

Reason:

- Served temp band compliance is already 99.0% post-sync.
- The pinched corridor drops solar-noon-to+5h temp compliance from 97.1% to
  75.1%.
- That means the controller is spending effort against a target line while the
  crop-tolerance envelope is mostly satisfied.

My recommendation: push `band_track_fraction=0`, run at least 48-72h across a
clear day and a cloudy day, and grade outcome/cost/runtime rather than
target-distance.

### 3. Do not shift the summer curve later

The current target peak is already later than the measured indoor temp/VPD peak.
Moving it later would follow outdoor air temperature, but the greenhouse air and
wet demand peak earlier.

If the project rejects the float trial and keeps target tracking, shift the
target peak earlier by about 45-75 minutes. The least invasive anchor direction
is lowering the sunset target anchor, not moving solar noon:

- `temp_target_ss=75 F` would put the harmonic peak around solar noon +2h.
- The low/high temp anchors should move in parallel if this is done, preserving
  width.

I would not do this first, because it will make evening target cool-down more
aggressive while outdoor air is still warm. Floating is the better first move.

### 4. Treat VPD as the real summer tuning problem

VPD served-band compliance barely improves when the pinch is removed. Daytime
VPD is still dry around solar noon through +5h.

Two plausible paths:

- If crop physiology permits a higher high-light VPD, widen or lift the daytime
  VPD high edge modestly around solar noon to +4h. This aligns with natural high
  light/high transpiration and avoids wasting water fighting every dry-side row.
- If that VPD is not acceptable for the crop, keep the band and improve wetting
  effectiveness in the +1h to +4h solar window. The mechanical-response review
  found wet equipment is the observed best dry-side correction.

Do not move the entire VPD curve later. Wet demand and VPD peaks are around
solar noon +2h, not +4h.

### 5. Keep current dawn/midday offsets for now

Observed live values:

- `dawn_boost_offset_min=60`.
- `midday_boost_offset_min=60`.
- `midday_drench_window_min=11`.
- `dawn_rehydrate_window_min=12`.
- `night_vpd_bias_kpa=0`.

The active midday window at solar noon +60 minutes is a reasonable anticipatory
lead for the wet-demand peak around +105 to +135 minutes. I would not move it
until root-zone/water outcome data says the pre-load is wasted.

The dawn window at sunrise +60 minutes is also defensible: it prehydrates before
the main dry-side rise. A small trial to sunrise +30 could be justified if
morning dry-side misses persist, but it is lower priority than VPD daytime
policy and the float trial.

`wet_taper_before_sunset_min=120` is currently a no-op shim in the curve-only
wetting path, so tuning it will not change climate behavior.

### 6. Add a daily nature-alignment report

Create a daily rollup that stores:

- Device sunrise/noon/sunset and DB sunrise/noon/sunset.
- Measured solar peak and solar centroid offset.
- Outdoor temp peak offset.
- Indoor temp and VPD peak offsets.
- Vent/fan/wet/heat duty peak offsets.
- Pinched and served corridor compliance.
- Runtime and water cost per solar phase bucket.

This should be the standard artifact before any future band-anchor edit. It will
separate "bad curve" from "cloudy day", "outdoor-air lag", and "actuator
capacity limit".

## Bottom Line

The greenhouse is already showing a physically coherent response:

- Sunlight peaks near solar noon.
- VPD/wetting demand follows roughly 1.5-2h later.
- Indoor temperature and vent demand follow roughly 3h later.
- Outdoor air temperature follows roughly 3.5h later.

The current summer target curve is plausible but a little late if interpreted as
a target to chase. Under the accepted ADR-0004 floating model, that is less
important: the served temperature corridor already fits nature well, and the
pinched target tracking is what makes temperature look worse than it is.

The biggest concrete bug is not the firmware curve. It is the DB seasonal solar
math. The biggest concrete tuning move is not a new anchor. It is to stop
pinching the temperature corridor, then focus VPD policy on the high-light dry
window.
