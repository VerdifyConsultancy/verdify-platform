# Wave 2 research (2026-11-05)

Jason asked for each research item to be data-driven, using historical production data, and backed by public research. Each report gives:
- the question;
- a bottom line;
- the queries it ran and their results, with the time window used;
- citations;
- the options considered;
- a recommended design;
- a PR-sized plan;
- the decisions Jason needs to make.

`docs/design/dashboard/decisions.md` records what Jason has decided. A report's recommendation stays a proposal until a row there settles it.

| Report | Issues | One line |
|---|---|---|
| [vpd-band.md](vpd-band.md) | #1365, #1334 | Lower the night VPD floor to 0.50 kPa. Outdoor-air "vent + reheat" pulses are the lever that works; heat alone barely dries the air. The band editor sets day value, night value and width. |
| [cortex-planner.md](cortex-planner.md) | #1366, #1350, #1346 | One deterministic call to Cortex Qwen with an input pack of about 20K tokens built around the forecast. Drop Iris lessons and self-scores. No embeddings retrieval in v1. |
| [planner-ab.md](planner-ab.md) | #1359, #1360, #1361 | A 60-day randomized day-pair switch: Cortex planner against frozen settings. The primary outcome is night VPD time in band, which can detect about 7–8 points. |
| [crops-locations.md](crops-locations.md) | #1327, #1329, #1328, #1330 | Keep `crops` and add cell occupancy plus layout fields. One service backs the API and MCP. Bulk re-plant. Plantings never feed control. |
| [local-only.md](local-only.md) | #1351, #1352, #1363, #1364 | An egress policy that allows private addresses only. Local copies of every image, plus pull secrets. NTP from the estate chrony server. Slack replaced by dashboard alerts and critical-only paging. |
