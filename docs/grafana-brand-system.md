# Grafana Embed Brand System

The public site embeds Grafana panels inside Quartz pages. Embedded panel chrome should look like part of Verdify Lab, while plot colors stay domain-coded for the physical system.

## Palette

| Site/Chrome Role | Color |
|---|---|
| Canopy / control band | `#2E7D32` |
| Leaf / compliant / actual greenhouse state | `#73BF69` |
| Telemetry mint / neutral active state | `#26A69A` |
| Deep navy embed surface | `#112231` |
| Slate text / dark neutral | `#78909C` |
| Graph gray / forecast / outside context | `#8C8C8C` |
| Glass neutral | `#B0BEC5` |
| Sunshine yellow | `#FDD835` |
| Solar forecast yellow | `#FFE66D` |
| Fault red accent | `#EF5350` |

## Series Colors

Series colors are not forced into the site palette. They stay domain-coded:

| Domain | Canonical Treatment |
|---|---|
| Solar / DLI / lux / sun | sunshine yellow fill; solar forecast uses lighter yellow. Daylight backdrops are fill-only with no plotted line. |
| Forecast / outside context | gray unless the distinction is solar-specific. Forecast lines are dashed where paired with observed values. |
| Electric cost/load | green |
| Gas / heat | orange-red / red |
| Water / irrigation | blue |
| Fog actuator overlays | cyan-blue, distinct from east-zone blue |
| Fans / vents / cooling | teal / blue; fan overlays avoid reusing west-zone teal |
| VPD | purple for indoor/plant pressure; outdoor VPD is gray context |
| Misters | distinct purple / pink / magenta by zone |
| Hydroponic chemistry | pH is chemistry yellow with impossible values filtered from public panels; ORP is violet on the secondary axis |
| Hydroponic temperature | water blue, indoor air green, outdoor context gray/dashed |
| Zones | south green, east blue, west teal, center mint |
| Weather context | wind speed blue, gusts gold, lulls pale neutral; clouds use neutral gray variants |
| Compute hosts | Cortex purple, Sentinel blue, Immich pink |
| Control bands / thresholds | safe bands stay green. Severity thresholds preserve blue/green/yellow/red progression and must not collapse to one semantic color. |

## Embedded Panel Rules

- Site markdown is the source of truth for embedded panel IDs: `/mnt/iris/verdify-vault/website/**/*.md`.
- Only panels embedded with `https://graphs.verdify.ai/d-solo/...panelId=...` are automatically branded.
- Embedded panels use transparent Grafana panel chrome on the active light/dark Grafana theme. Quartz `.grafana-embed` / `.pg` wrappers are layout only.
- Any embedded panel whose SQL uses Grafana's `$__timeFilter` must carry an explicit `from=...&to=...` range in the site URL. Do not rely on the dashboard default range for public embeds.
- The site must not add its own border, radius, background, or shadow around the iframe. Grafana owns the single visible panel boundary; Quartz wrappers are layout only.
- Homepage explanatory panel wrappers are also layout only: no outer card border, stripe, fill, padding, or shadow around the Grafana embed.
- Stat panels use value color rather than full background color.
- Timeseries and bar panels keep their query shape and plot style, but normalize axis color mode, legend placement, legend calculations, and tooltip behavior.
- Public legends must use domain terms such as `Water Used (gal)` or `Flow (gal/min)`, not raw SQL aliases like `value`.
- Daily categorical bar panels use compact date labels at embedded width; do not leak full ISO dates into the x-axis.
- Table panels use headers, compact rows, no footer, automatic cell formatting, title-case public labels, explicit widths for compact public tables, and truncated free-text summaries where long database text would clip in the embed.
- Stat panels must show public units directly when the title alone is not enough. Runtime, stress-hour, and plan-age summaries use `h`; controller heap uses `kB`; costs use `$`.
- Bargauge and state-timeline panels keep domain-coded series colors just like timeseries panels.
- Fixed series colors are mapped by semantic labels such as solar, forecast, heat, gas, water, fan, threshold, target, VPD, and fault. Do not replace these with the site palette or a generic palette cycle.
- Zone comparison panels keep zone colors for zone sensor lines. Equipment overlays in those same panels use actuator-family shades that do not reuse the zone colors.
- The homepage lighting panel uses the solar/daylight fill as context and hides the historical `Natural Lux (10m avg)` trace from the public embed so it does not add a second yellow line over the filled backdrop.
- Homepage lighting renders only confirmed main/grow ON windows as line-free opacity fades; OFF periods are `NULL` and therefore transparent. Do not render policy threshold lines or hysteresis bands on this summary panel. Detailed ON/OFF threshold evidence remains on `site-climate-lighting` panel 17.
- VPD context is semantic, not palette-driven: indoor/actual indoor VPD is purple, observed outdoor VPD is solid gray, and outdoor VPD forecast is dashed gray.
- VPD labels win over neighboring humidity/dew-point context; a panel title containing dew point must not recolor a `VPD` series as humidity teal.
- Mixed VPD/dew-spread panels pin VPD to the left `kPa` axis and dew spread to a right `°F` axis.
- Mixed soil-moisture/VPD panels pin soil moisture to the left `%` axis and air VPD to a right `kPa` axis; VPD remains purple even when the series is zone-qualified.
- Light transmission belongs to the lighting family and uses sunshine yellow, matching DLI, lux, and solar context rather than a generic palette color.
- Lighting circuit identity stays explicit when main/grow need to be distinguished: main uses green, grow uses blue, while daylight/sun/occupancy context remains yellow.
- Soil probe identity stays stable across stat and trend panels: south 1 green, south 2 gold, and west teal. Soil EC uses conductivity teal.
- Reliability/fault traces use fault red, with panic/reset variants allowed to use the gas-red family so they remain distinguishable.
- Flow/totalizer panels split instantaneous water flow from cumulative water total with blue shades and separate axes.
- Mister zone runtime uses the same south/west/center mister colors as VPD-control overlays.
- Resource Use electric cost, greenhouse load, daytime/night watts, and kWh/day summaries use electric green. Powerwall coverage and AI/GPU compute remain separate mint/host-coded signals.
- Planning Quality compliance/stress series must not inherit VPD purple just because the panel title mentions VPD. Temperature compliance/stress is heat orange, VPD compliance/stress is purple, total compliance is green, and total stress is fault red.
- Planning Quality accuracy/tradeoff panels use the same semantics: compliance green, temperature MAE orange, VPD MAE purple, stress red, cost green, water blue, mister water purple, and mister runtime rose.
- Crop stress stats use a three-way semantic set: VPD stress is purple, heat stress is heat orange, and cold stress is cool blue.

## Commands

Apply the branding pass:

```bash
python3 scripts/brand-grafana-embeds.py
```

Validate embedded dashboard JSON:

```bash
make grafana-brand-check
```

Validate the live Grafana DB copy that the public embeds actually render:

```bash
make grafana-brand-check-live
```

`make site-doctor` also runs the brand check after the normal site/Grafana embed audit.

For k3s/GitOps deploys, edit `grafana/dashboards/site-*.json`, run
`python3 scripts/gen-grafana-dashboard-cms.py`, and commit the regenerated
`deploy/k8s/components/grafana/generated/dashboards-cm-*.yaml` files. ArgoCD
applies those ConfigMaps; do not patch live Grafana dashboards by hand.
