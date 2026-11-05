# W5 research: crops and locations

Track `crops-locations`. Issues: #1329 (reconcile locations and plantings with the physical greenhouse and lab.verdify.ai) and #1327 (planting model on the location tree, served from the operator API). It informs #1328 (MCP planting tools) and #1330 (Crops, approved as the sixth top-level section).

Evidence date: 2026-11-05, read-only SQL on `verdify-db-0` and code at origin/main `cf21fa421`.

## Question(s)

1. What do the location, position, crop and planting tables hold today, and how far is that from the greenhouse? Jason, 2026-11-05: the hanging Vandas are the only crop, and the hydro rails are empty.
2. What location tree should the Crops page and the MCP tools draw from? It covers the zones, the walls and shelves, the 60 hydro cells, the hanging spots and the irrigation lines that reach each one.
3. Which data model records a planting across many cells, with plant, move, harvest and remove events?
4. What re-plant workflow makes recording a whole re-plant fast, on the dashboard and through an agent?
5. What migration and reconciliation get from today's rows to "Vandas are the only active planting" without losing history?

## Bottom line

- **Keep `crops` as the planting row and add an occupancy table** (option A in #1327): `crop_occupancy(crop_id, position_id, during tstzrange, count)`, with an exclusion constraint so that no two plantings hold one cell at the same time. One planting can cover 10 or 30 cells, a move closes one range and opens another, and history stays intact. Replacing `crops` (option B) would rewrite 22 dependent views and 8 foreign-key tables for no gain.
- **Draw the layout from data.** Add `positions.layout_group / layout_row / layout_col` and `shelves.wall / display_order`. Seed the 96 existing positions in the same migration. The hydro cell order (which rail is A, which end is 1) needs a one-line confirmation from Jason (decision D1). The default follows lab.verdify.ai: cells 1–30 in the top row, 31–60 in the bottom row, and rails A/B/C as the thirds of each row.
- **Record which line waters each place**, as informational shelf-to-line rows keyed to `irrigation_line.line_id`, which is the configured-relay source of truth. Wall drip L01/L02 covers every east, south and west shelf. The misters cover center (L04), south (L07) and west (L09). The hydro rack has no relay line. Center drip L05/L06 is `reserved` and covers nothing until Jason says otherwise. Nothing in control reads these rows.
- **End three stale active rows now, as a reconciliation** rather than a harvest: pepper `EAST-HYDRO-41`, Cannabis "SOUTH-FLOOR" and Lime Tree "WEST-FLOOR". The Vandas then become the only active planting, on the center hanging spots. These rows cause live harm today:
  - In the last 30 days the vision job wrote 460 health observations against these three nonexistent plants (pepper 110, cannabis 119, lime 231).
  - They raised 14 `soil_dryout` alerts on south for a plant that is not there.
  - They set the east Statistics grading band to the pepper profile.
- **One write path.** A new `verdify_services/crops.py` handles every write: it resolves positions, checks conflicts and keeps writes idempotent through `activity.recorded_write`, exactly like `verdify_services/irrigation/writes.py`. The operator API and MCP are thin layers over it. The legacy `api/main.py` crop writes cannot write live anyway, because `verdify_api_runtime` is SELECT-only; retire them.
- **Make a re-plant cheap** with a shared position resolver. It accepts `hydro 1-30`, `EAST-HYDRO-1..10`, `rail A top`, `west top 1-3` and `all south shelves`. The resolver powers both the dashboard's multi-select "Plant…" dialog and the MCP `record_planting` tool, and offers a preview mode. Version 1 needs no template table: the layout groups (rail, row, shelf, wall) are the templates, plus "copy a previous planting".
- **Plant-agnostic boundary:** plantings are records only. A drift test proves that no control function or dispatcher query reads `crops` or `crop_occupancy`. The east grading band moves off active crops onto the band in force; coordinate that with the band-compliance research.

## Evidence from our data

Window: the whole life of the topology and crop tables, from the vault import by migration 085 (first event 2026-03-26) to 2026-11-05. Every row these tables have ever held is in scope; they are small. The observation and alert counts use the last 30 days to show current effect. The hydro probe series uses 210 days, its full stored span.

### Q1. The location tree (zones, shelves, positions)

```sql
SELECT z.slug, s.slug, s.kind, s.tier, s.position_scheme, count(p.id), min(p.label), max(p.label), string_agg(DISTINCT p.mount_type,',')
FROM zones z LEFT JOIN shelves s ON s.zone_id=z.id LEFT JOIN positions p ON p.shelf_id=s.id GROUP BY 1,2,3,4,5 ORDER BY 1,2;
```
```
center center_floor      floor 0 CENTER-FLOOR-{N}        4 pot
center center_hang       hang    CENTER-HANG-{1|2}       2 hanging_hook
east   east_hydro        nft     EAST-HYDRO-{1..60}     60 nft_port
east   east_shelf_bottom shelf 0 EAST-SHELF-{T|B}{1..3}  3 shelf_slot
east   east_shelf_top    shelf 1 EAST-SHELF-{T|B}{1..3}  3 shelf_slot
north  (none)
south  south_floor       floor 0 SOUTH-FLOOR-{N}         4 pot
south  south_shelf_bottom shelf 0 SOUTH-SHELF-{T|B}{1..4} 4 shelf_slot
south  south_shelf_top   shelf 1 SOUTH-SHELF-{T|B}{1..4} 4 shelf_slot
west   west_shelf_bottom shelf 0 WEST-SHELF-{T|B}{1..6}  6 shelf_slot
west   west_shelf_top    shelf 1 WEST-SHELF-{T|B}{1..6}  6 shelf_slot
```
There are 96 positions. Columns (from `information_schema.columns`):
- `positions(id, greenhouse_id, shelf_id, label, slot_number, mount_type, is_active, notes, created_at)`: no rail, row or column.
- `shelves(…, kind, tier, position_scheme, …)`: no wall or display order.
- Constraints: `positions` label is unique per greenhouse; `mount_type` is one of pot, shelf_slot, hanging_hook, nft_port, hydro_raft, direct_ground; `shelves.kind` is one of floor, shelf, hang, rack, nft, hydro.

### Q2. Crops, events and catalog

```sql
SELECT c.id,c.name,c.variety,c.position,c.zone,c.position_id,p.label,c.crop_catalog_id,c.planted_date,c.stage,c.count,c.is_active,c.cleared_at::date
FROM crops c LEFT JOIN positions p ON p.id=c.position_id ORDER BY c.id;
```
```
 2 Canna Lilies              SOUTH-FLOOR   81 SOUTH-FLOOR-1  2025-01-01 cleared    f 2026-05-30
 3 lettuce                   EAST-SHELF-T1 67 EAST-SHELF-T1  2026-03-01 seedling   f 2026-06-11
 4 House Plants              WEST-SHELF    -                 2025-01-01 cleared    f 2026-03-26
 5 Vanda Orchids Mixed Vanda CENTER-HANG    1 CENTER-HANG-1  2026-04-02 vegetative t  count 1
 6 Test Basil Genovese       HYDRO-01       7 EAST-HYDRO-1   2026-04-06 seedling   f 2026-04-07
 8 strawberry                HYDRO-31      37 EAST-HYDRO-31  2026-04-01 seedling   f 2026-06-11
 9 pepper                    HYDRO-41      47 EAST-HYDRO-41  2026-04-01 seedling   t
10 E2E Test Tomato Sungold   SOUTH-FLOOR-2 82 SOUTH-FLOOR-2  2026-04-19 vegetative f 2026-04-19
14 Cannabis                  SOUTH-FLOOR   -  (no position)  2026-06-10 vegetative t
15 Lime Tree                 WEST-FLOOR    -  (no position)  2026-06-10 vegetative t
```
There are 4 active rows, and only 1 of them (the Vandas) is real. The free-text `position` drifts from the labels (`HYDRO-41` versus `EAST-HYDRO-41`, and `WEST-FLOOR` has no row). The Vandas are recorded as `count 1` on one hook, although the planting event says "Multiple Vanda orchids hung bare-root".

```sql
SELECT e.ts::date, e.crop_id, e.event_type, e.source, e.operator, e.position_id, left(e.notes,60) FROM crop_events e ORDER BY e.ts;
```
This returns 21 rows from 2026-03-26 to 2026-06-11, and nothing since. The event types are planted, stage_change, removed and note. The sources are backfill 4, trigger 10, api 4, slack 1, iris 1 and migration 1. Each `is_active` flip writes a trigger "removed" row, and migration 145 wrote one by hand, so canna has two removed events.

```sql
SELECT id,slug,common_name,category,cycle_days_min,cycle_days_max FROM crop_catalog ORDER BY id;
```
The catalog has 11 slugs: basil, canna, cucumbers, herbs, lettuce, peppers, strawberries, tomatoes, orchid, cannabis, citrus. **Bug:** every `common_name` is a page title such as "Lettuce in an Automated Greenhouse", so a picker or an MCP answer would show that title. There is no `romaine` and no variety vocabulary; variety is free text on `crops`.

### Q3. Constraints, triggers, dependents and grants

- Triggers on `crops` (`pg_trigger`):
  - `trg_crops_log_planted` (AFTER INSERT), `trg_crops_log_clear` (BEFORE UPDATE OF is_active) and `trg_crops_log_stage_change` write `crop_events`.
  - `crops_capture_assignment_revision` (#782, SECURITY DEFINER) and `crops_no_unaudited_truncate` keep the lineage.
  - Migration 255 seals these trigger definitions into the login attestation digests, so the design below **adds no trigger to `crops`**.
- Indexes: `idx_crops_active_position UNIQUE (greenhouse_id, position_id) WHERE is_active AND position_id IS NOT NULL` gives one active crop per position. It is bypassed whenever `position_id` is NULL, as for crops 14 and 15.
- Foreign keys into `crops`: `crop_events`, `crop_tasks` (cascade), `harvests`, `lab_results`, `nutrient_recipes`, `observations`, `treatments`. Foreign keys into `positions`: the same tables plus `sensors`.
- Views depending on `crops` (`pg_depend` through `pg_rewrite`): 22 views reference the crop and location tables:
  - In `public`: `v_crop_history`, `v_crop_lifecycle`, `v_crop_lifecycle_completeness`, `v_crop_health_trend`, `v_gdd`, `v_harvest_story`, `v_iris_experiment_context`, `v_position_current`, `v_slack_crop_tasks_due`, `v_succession_gaps`, `v_succession_plan_readiness`, `v_succession_timeline`, `v_zone_full`, `v_topology_tree`.
  - `verdify_lab_publisher_runtime.{crops,crop_events,crop_catalog,positions,shelves,zones}`.
  - `verdify_vision_runtime.crops`.
- `btree_gist` is available (1.7) but not installed. Migrations run as the superuser `verdify`.
- Grants (`information_schema.role_table_grants` and `has_*_privilege`):
```
verdify_api_runtime       crops/crop_events/harvests/positions/shelves/zones/v_position_current/v_topology_tree: SELECT only
verdify_dashboard_runtime activity_log INSERT,SELECT; nothing on any crop or location table
verdify_mcp_runtime       crops INSERT,SELECT,UPDATE; crop_events INSERT,SELECT; harvests INSERT,SELECT;
                          zones, v_position_current, v_topology_tree SELECT; positions, shelves: none
                          sequences crops_id_seq / crop_events_id_seq: USAGE
```
- Confirmed bugs:
  - (a) The `create` action of the MCP `crops` tool (`mcp/server.py` from line 4851; the INSERT is about 60 lines in) writes only the free-text `zone`/`position` and drops `position_id`, `zone_id` and `crop_catalog_id`, although `CropCreate` carries them (`verdify_schemas/crops.py:77-82`). A row created through MCP never occupies a position.
  - (b) Every legacy `api/main.py` crop write would fail with permission denied, because the API role is SELECT-only.
  - (c) The MCP audience map (`mcp/server.py:1246`) gives `crops` only to `iris` and `admin`, so Jason's named agent (orbit) cannot record a planting.

### Q4. Effects of the stale active rows today

```sql
SELECT o.crop_id, c.name, count(*), min(o.ts)::date, max(o.ts)::date, count(*) FILTER (WHERE o.ts>now()-interval '30 days')
FROM observations o JOIN crops c ON c.id=o.crop_id GROUP BY 1,2 ORDER BY 1;
```
```
 5 Vanda Orchids 585  2026-04-06 2026-11-05  n30=145
 9 pepper        454  2026-04-10 2026-11-05  n30=110
14 Cannabis      246  2026-07-03 2026-11-05  n30=119
15 Lime Tree     481  2026-07-03 2026-11-05  n30=231
```
The vision job puts every active crop into its prompt (`deploy/k8s/vision/src/analyze-greenhouse-snapshot.py:77-90`). It then matches each model-named crop with `name ILIKE` against active rows (lines 192-215) and inserts a `visual_health` observation with a health score. So the model is being told that a lime tree, a cannabis plant and a pepper exist, and it scores their health, which means 460 fabricated observations in 30 days.

```sql
SELECT alert_type, zone, count(*), max(ts)::date, count(*) FILTER (WHERE resolved_at IS NULL)
FROM alert_log WHERE ts > now()-interval '30 days' AND alert_type ~ 'soil' GROUP BY 1,2 ORDER BY 3 DESC;
```
```
soil_sensor_offline (no zone) 299 · soil_dryout south 14 (last 2026-11-04) · soil_sensor_offline south 2 · west 1
```
`ingestor/tasks/alerts.py:2250-2253` takes "zone has an active crop" as soil-probe occupancy. Because of the phantom cannabis on south, south dryout alerts still fire.

`ingestor/tasks/daily.py:269-289` builds the east grading band from the active east crops' `crop_target_profiles`. Today that set is just the stale pepper. Ending it makes east fall back to `DEFAULT_ZONE_BAND`, so east Statistics grades will shift on the day of the reconciliation; note that in the PR.

### Q5. Irrigation lines (the configured relays)

```sql
SELECT line_id,name,zone,kind,relay,equipment,water_system,reserved,enabled FROM irrigation_line ORDER BY 1;
```
```
L01 Drip • Wall              wall   drip       enabled      L02 Drip • Wall (Fert.)   wall   fert_drip
L03 Valve • Fert. Master     supply master                  L04 Mister • Center       center mister  enabled
L05 Drip • Center            center drip   reserved         L06 Drip • Center (Fert.) center fert_drip reserved
L07 Mister • South Wall      south  mister                  L08 Mister • South Wall (Fert.) south fert_mister
L09 Mister • West Wall       west   mister                  L10 Mister • West Wall (Fert.)  west  fert_mister
L11 Fog (AquaFog XE 2000)    house  fog
```
Of these, only L01 and L04 are enabled. `water_systems.wall_drip_clean` says "Shared: south + west zones", which contradicts Jason's fact that the wall drips also cover the east wall. That note is stale and should be corrected in the same migration. No relay feeds the hydro rack; it is a recirculating loop dosed by hand (lab `/greenhouse/hydroponics`).

### Q6. Is the hydro rack empty? Probe history

```sql
SELECT time_bucket('30 days', ts)::date, count(*), avg(hydro_ph), avg(hydro_ec_us_cm), avg(hydro_water_temp_f)
FROM climate WHERE ts > now()-interval '210 days' AND hydro_ph IS NOT NULL GROUP BY 1 ORDER BY 1;
```
```
2026-04-16 7251 pH 7.23 EC 2478 wt 75.3   …   2026-08-14 1003 pH 2.66 EC 2478   2026-10-13 1298 pH 7.78 EC 2478 wt —
```
EC has only 2 distinct values over 210 days, water temperature stopped on 2026-09-08 and the last row is 2026-11-02. The probe data are not live readings, which fits an empty, idle rack. #1183 (the YINMIK reader) owns this. The Crops page must not show hydro chemistry as a status of the plantings.

### Q7. Physical topology from lab.verdify.ai and the repo

- `/greenhouse/hydroponics` (fetched 2026-11-05): a 60-position recirculating NFT rack on the east wall with 3 PVC rails in 2 rows. It uses rockwool in net cups and 14 Barrina 2 ft LEDs, dosing is manual, and one YINMIK probe measures the reservoir. The page gives no numbering.
- `/greenhouse/zones/east`, as quoted in #1329: "4” PVC, 3 rails (A, B, C)", positions 1–30 in the top row and 31–60 in the bottom; EAST-SHELF B1–B3 are partly blocked by the tank.
- `scripts/generate-hydro-map.py:129-133` instead labels "Top Rail (Front) 1–30" and "Bottom Rail (Back) 31–60". So the repo has two readings of "rail", which is why D1 asks Jason.
- `/greenhouse/zones/center` (fetched): "Center Hang", `CENTER-HANG-{1|2}`, mixed Vandas; the number of hooks or plants is not stated. Center misters have 5 heads and 25 nozzles, overhead. Center drip "wets the hanging/root-zone path".
- The lab zone and crop pages are rendered from these same tables plus hand-written prose (`scripts/render-zone-pages.py`, `scripts/render-crop-profiles.py`). The `/greenhouse/crops` hub is hand-written and still lists lettuce and strawberry as active.

## Public research

Crop-inventory data models:
- farmOS log types (https://farmos.org/model/type/log/): Seeding, Transplanting, Harvest, Observation, Input and others are logs against assets, and any log flagged `is_movement` changes where its assets are. Quantities are separate records attached to logs. This supports history-as-events plus a computed current location.
- farmOS location logic (https://farmos.org/model/logic/location/): a movable asset's location is computed from its done movement logs, while pending movements give a projected location. This supports keeping occupancy as time ranges and treating "planned" as a later extension.
- farmOS asset types (https://farmos.org/model/type/asset/): Land and Structure assets nest (field > bed), and a Plant asset carries a plant type and a season. This supports the zone > shelf/rail > cell tree with a crop-type catalog.
- LiteFarm Crops v2 (https://lite-farm.atlassian.net/wiki/x/CAC2K; search snippet only): crop type, then varietal, then a "management plan" per planting keyed by varietal, location, seed date and harvest date. A person marks a plan completed or abandoned. This supports a catalog plus free-text variety, and an explicit end reason rather than an expiry.
- Metrc DC bulletin 19 (https://www.metrc.com/bulletin/dc-bulletin-19-plants-features-and-best-practices): immature plants are tracked as batches, a move is an explicit "change rooms" action, and partial harvests ("manicure") are recorded against a batch that stays alive. This supports one planting per batch, move events, and partial harvest as an event with a count.
- Metrc planting records (https://www.metrc.com/metrc-planting-records-from-mother-plant-to-immature-batch/; snippet): batches without individual tags are allowed up to a size threshold. This supports tracking by batch, not by plant, for a 60-cell hobby rack.
- Pattern across these tools: batch-level plantings, a location hierarchy, append-only events with a derived or cached current state, moves and partial harvests as events, and plans or templates as an optional layer.

NFT layout and cycles:
- Cornell CEA, "Leafy Greens", GPN 2016 (https://cea.cals.cornell.edu/files/2019/06/GPNOctober2016_LeafyGreens.pdf): NFT channels slope 1–4%. A lettuce head is ready about 24 days after transplant at a DLI of 17 and about 35 days from seed. NFT buffers temperature and EC swings less than deep water culture. This sets the scale for succession planting (a third to a half of the rack every 1–2 weeks) and is why "days in place" is the main cell label.
- Greenhouse Management textbook, ch. 15 (https://greenhouse-management.com/greenhouse_management/greenhouse_production_systems/nutrient_film_technique.htm): about 1 L/min per channel at a 1.2–3% slope; fruiting crops need more flow than lettuce.
- CropKing NFT lettuce (https://cropking.com/blogs/knowledge-center/nft-production-lettuce; vendor): best slope about 1.5%; long runs deplete nutrients and oxygen toward the far end. This supports recording cells by rail and column so that end-of-rail effects can later be compared.
- Nutrient film technique overview (https://en.wikipedia.org/wiki/Nutrient_film_technique; secondary, snippet): lettuce goes 6–8 in apart.
- Rise Gardens spacing guide (https://risegardens.com/blogs/communitygarden/hydroponic-plant-spacing-guide-for-vegetables; vendor, snippet): basil 4–6 in; tomatoes and peppers 12–18 in plus support. So a large plant can shade or block neighbouring cells, and the model should let one plant occupy more than one cell.
- Virginia Tech SPES-466 (https://pubs.ext.vt.edu/SPES/spes-466/spes-466.html; snippet) and Vertical Farm Daily (https://www.verticalfarmdaily.com/article/9804369/applying-nutrient-film-technique-systems-to-alternative-cea-crops/; trade press): NFT is very good for leafy greens, and fruiting crops are a weaker fit without changes. This is context for the lab sketch that put peppers and strawberries in the rack; it is not a control input.

Vanda culture:
- UF/IFAS Charlotte County, "Vanda orchids at a glance" (https://blogs.ifas.ufl.edu/charlotteco/2021/12/23/vanda-orchids-at-a-glance/): Vandas are epiphytes grown in slatted hanging baskets with little or no mix. Their roots should dry between waterings, and they need protection below 55 °F. This supports a hanging location type whose spots hold baskets, each possibly with several plants. The AOS care sheet URL returned 404.

## Options considered

| Option | What | For | Against |
|---|---|---|---|
| A. `crops` stays the planting, plus `crop_occupancy` | One row per planting (batch) and one occupancy range per cell | Keeps 22 views, 8 FK tables, the #782 lineage and the 255 attestation untouched. Additive. Backfill is trivial (2 rows with a position). | `crops.position_id` and the free-text columns stay as compatibility columns. `crops` keeps legacy agronomy columns (`target_vpd_*`, `base_temp_f`) that the plant-agnostic rule no longer uses. |
| B. New `plantings` + `planting_positions` + `planting_events`, `crops` as a view | Clean names | Clean | `crops` becomes a view, and every FK, the triggers, the lineage and the attestation receipts must move. Large migration, high risk to the ledgered migrate hook. |
| C. Keep one row per cell | Status quo | No schema work | A 30-cell re-plant is 30 rows and 30 MCP calls, moves leave no history, and Jason asked for a cheap bulk re-plant. |

Occupancy uniqueness:
- An `EXCLUDE USING gist (position_id WITH =, during WITH &&)` constraint needs `CREATE EXTENSION btree_gist`. It is available, and migrations run as superuser. It forbids overlap in backdated history as well.
- A partial unique index on open occupancy needs no extension but checks only current state.
- Recommendation: use the exclusion constraint. Backdated entries ("I planted these last Tuesday") are exactly the writes that would otherwise overlap silently.

Templates:
- (i) A `planting_templates` table.
- (ii) Use the layout groups as selectors (rail, row, shelf, wall, "all hydro") plus "copy planting #N".
- Recommendation: (ii) for version 1. The existing `succession_plan` table (0 rows) is the place for scheduled succession later.

## Recommended design

### Data model (one migration, next free number after the W5 queue; today's last is 289)

1. `CREATE EXTENSION IF NOT EXISTS btree_gist;`
2. Layout:
   - `shelves`: add `wall text CHECK (wall IN ('north','east','south','west','center'))`, `display_order smallint` and `label_short text` (for example "Hydro", "West top").
   - `positions`: add `layout_group text` (hydro rail `A|B|C`; south bay `SE|SW`), `layout_row smallint` (0 = top or front) and `layout_col smallint` (1-based, left to right as seen from the walkway).
   - Seed all 96 positions in the same migration:
     - Hydro default (D1): `layout_row = 0` for cells 1–30 and 1 for 31–60; `layout_col = ((n-1) % 30) + 1`; `layout_group = A/B/C` for columns 1–10, 11–20 and 21–30.
     - Shelves: `layout_row = 0` for top and 1 for bottom; `layout_col` = bay number; south bays 1–2 are SE and 3–4 are SW (D2).
     - Center hang and floor: one row each.
3. Hanging: keep `CENTER-HANG-1..2` as `hanging_hook` positions (D3 may add hooks). A hanging spot can hold a basket of several plants through the occupancy `count`.
4. `crop_occupancy`:
   - Columns: `id bigserial`, `crop_id int NOT NULL REFERENCES crops`, `position_id int NOT NULL REFERENCES positions ON DELETE RESTRICT`, `during tstzrange NOT NULL CHECK (NOT isempty(during) AND lower(during) IS NOT NULL)`, `count int CHECK (count >= 0)`, `greenhouse_id`, `created_at`.
   - `EXCLUDE USING gist (position_id WITH =, during WITH &&)`.
   - Open occupancy is `upper(during) IS NULL`.
5. `crops` additions: `end_reason text CHECK (end_reason IN ('harvested','failed','removed','moved_out','reconciled'))`, `source text` and `actor text`.
   - The existing columns stay: `cleared_at` is the end time, `stage` is optional, and the agronomy columns stay but are unused.
   - `position_id` becomes the planting's **anchor cell**, the lowest-numbered open cell, maintained by the service, so old readers keep working. A batch with no open cell has NULL.
   - Before merging, check whether the 255 login digests cover column definitions as well as trigger definitions. If they do, advance the receipts in this migration (Q-08 pattern).
6. `crop_events.event_type` vocabulary, enforced by the service and `verdify_schemas`:
   - Existing: `planted`, `stage_change`, `removed`, `note`.
   - New: `moved`, `harvested` (partial or full, with `count`) and `reconciled`.
   - Add `request_id text` and `detail jsonb`, holding the cells for a move, a partial harvest or a plant.
   - The existing triggers keep writing planted and removed on insert and on the `is_active` flip. The service writes the richer events, so no trigger changes.
7. `shelf_water_lines(shelf_id, line_id REFERENCES irrigation_line(line_id), note)`: informational coverage. Seed:
   - `east_shelf_top/bottom`, `south_shelf_top/bottom`, `west_shelf_top/bottom` → L01, L02.
   - `south_shelf_*`, `south_floor` → L07, L08.
   - `west_shelf_*` → L09, L10.
   - `center_hang`, `center_floor` → L04.
   - `east_hydro` → none (recirculating).
   - `south_floor` → wall drip only if D4 says so.
   - Correct the `water_systems.wall_drip_clean` note to "east, south and west walls and all wall shelves".
   - Control never reads this table.
8. Views:
   - `CREATE OR REPLACE v_position_current` with the same columns, joined through open `crop_occupancy` rather than `crops.position_id`, plus `layout_*`, `wall`, `occupancy_count` and `water_lines`.
   - Add `v_crop_planting_current`: one row per active planting with its cell array and count.
   - Add `crop_occupancy` and the layout columns to `verdify_lab_publisher_runtime`.
9. Grants:
   - `verdify_dashboard_runtime`: SELECT on zones, shelves, positions, crops, crop_events, crop_occupancy, crop_catalog, harvests, shelf_water_lines, irrigation_line and the views; INSERT/UPDATE on crops and crop_occupancy; INSERT on crop_events and harvests; USAGE on their sequences.
   - `verdify_mcp_runtime`: SELECT on positions, shelves, crop_occupancy and shelf_water_lines; INSERT/UPDATE on crop_occupancy.
   - Leave `verdify_api_runtime` SELECT-only.
10. Backfill:
    - One open occupancy for each active crop that has a `position_id`: crop 5 at CENTER-HANG-1 and crop 9 at EAST-HYDRO-41, from `planted_date`.
    - One closed occupancy for each ended crop with a position, ending at `cleared_at`, so cell history shows lettuce, strawberry, basil, canna and the test tomato.
11. Catalog clean-up: set `common_name` to the plain crop names (Basil, Canna lily, Lettuce, …).

### Service (`verdify_services/crops.py`)

- `resolve_positions(text | list) -> [position]`:
  - Accepts exact labels, `LABEL..LABEL`, `hydro 1-30`, `rail A [top|bottom]`, `west top 1-3`, `south shelf bottom 2`, `all <shelf|wall|zone>` and `hang 1`.
  - Ambiguous input returns candidates and writes nothing.
  - One parser serves the dashboard text box, MCP and the reconciliation script.
- Writes, each one `activity.recorded_write` (an activity_log row plus the domain rows in one transaction, replayed by `request_id`):
  - `plant(crop, variety, positions, count_per_cell=1, planted_at, notes, replace=False)`.
  - `move(planting, from?, to, at)`, which closes the old ranges and opens new ones.
  - `harvest(planting, positions?|count?, at, weight?, final?)`, which writes `harvests` rows and a `harvested` event and closes those cells if final.
  - `end(planting, reason, at)`.
  - `annotate`.
  - `update(stage?, count?, variety?, notes?)`.
- Conflicts return a typed `position_occupied` error with the cell list. `replace=True` ends the occupant (`moved_out`, or `removed`) in the same transaction.
- Times default to now in America/Denver, and backdating is allowed. The exclusion constraint guards the result.
- Plant-agnostic: the service never touches `crop_target_profiles`, `crop_band_anchors` or any setting.

### Operator API and BFF

Routes as listed in #1327:
- Reads: `GET /v1/crops/locations`, `/v1/crops/plantings`, `/v1/crops/plantings/{id}`, `/v1/crops/positions/{label}/history`, `/v1/crops/catalog`.
- Preview: `POST /v1/crops/resolve`, which returns the cells and conflicts and writes nothing; the dashboard dialog and the MCP dry run both use it.
- Writes: plant, move, harvest, end and patch, for the operator role behind `DASHBOARD_WRITES_ENABLED`; a viewer gets 403.

### Dashboard (`/crops`, #1330)

- **Map**: one card per wall, drawn from `wall`, `display_order` and the `layout_*` columns:
  - The hydro rack is 2 rows × 30 columns with rail dividers A|B|C.
  - The shelves are 2 tiers × bays. The SE and SW south bays are split. The center card shows hanging spots and floor pots.
  - A cell shows a short crop name and days in place, coloured by crop. A cell drawer shows the cell's history and the lines that water it.
- **Selection**:
  - Click, shift-click for a range or drag across cells.
  - Group chips select a group in one tap: "Rail A", "Top row", "All hydro", "West top".
  - A text box takes the resolver syntax (`hydro 1-30`).
- **Plant… dialog**:
  - Fields: crop (catalog typeahead, or add a new catalog entry), variety (free text with recent values), count per cell (default 1), planted date (default today), notes.
  - It shows the resolved cells and any conflicts, with a "replace occupant" switch. Save writes one planting.
  - "Plant again" pre-fills from a chosen past planting. This is the template.
- **Re-plant mode**, for Jason's restart:
  - A sticky crop and date, so successive selections each save a planting in two taps.
  - A running tally ("42 of 60 hydro cells planted").
- Move…, Harvest… (pick cells or a count; partial by default) and Remove… (reason).
- **History**: plantings with crop, variety, cells (compressed as `EAST-HYDRO-1..10`), count, planted, ended, reason and actor.
- No crop-specific control copy.

### MCP (#1328)

The tools `crop_locations`, `resolve_positions`, `record_planting` (with `dry_run`), `move_planting`, `harvest_planting`, `remove_planting`, `annotate_planting` and `planting_history` sit over the service, open to the `agent` and `admin` audiences.
- Iris stays read-only on plantings.
- `crops.create` is removed or delegates to `record_planting`, so nothing writes a planting without occupancy.
- The reply always names the resolved cells and returns the planting id, so "undo" is `remove_planting(reason='removed')`.
- Writing directly by default and offering `dry_run` is simpler than a confirm round-trip.

### Downstream readers to repoint (same PR as the service)

- Vision (`analyze-greenhouse-snapshot.py:77-215`):
  - Build the prompt from `v_crop_planting_current`, with names and cells.
  - Attribute an observation only when its zone matches the planting's open cells. Today it does an `ILIKE` on the name across the whole house.
- Soil alerts (`alerts.py:2250`, `:2839`): keep "zone has an open planting", read from `crop_occupancy` joined to the zone. This rule is plant-agnostic.
- East grading (`daily.py:269`): stop keying on active crops and use the band in force, coordinated with the band-compliance research issue.
- Drift test: no `fn_*` used by control, `fn_zone_vpd_targets` or the dispatcher SQL references `crops` or `crop_occupancy`.

## Implementation plan

1. **PR 1, model and service (#1327)**:
   - The migration above (layout seed, occupancy, events, water-coverage rows, grants, backfill, catalog names, water_systems note).
   - `verdify_schemas` models (Planting, PlantingCreate, Move, Harvest, End, PositionRef).
   - `verdify_services/crops.py` with the resolver.
   - The operator API routes, the OpenAPI contract and `dashboard/app/generated/operator-api.ts`.
   - Repoint vision, the soil gate and the east band; add the drift test; retire the legacy `api/main.py` crop writes.
   - A decision note in `docs/design/dashboard/decisions.md`.
   - Tests: range resolution, conflict and replace, idempotent replay, move history, partial harvest, viewer 403. Rule 7: `Post-merge restart: verdify-mcp, verdify-ingestor` (schemas change).
2. **PR 2, MCP tools (#1328)**: the tools, audiences and `crops.create` removal described above. It can merge with PR 1 if the reviewers prefer one promotion.
3. **PR 3, Crops page (#1330)**: map, history, dialogs, re-plant mode, fixtures and in-cluster Playwright/axe.
4. **Reconciliation run (#1329)**, after PR 1 is promoted (migrate hook applied). This is data written through the service, not migration data:
   - `scripts/reconcile-crops-2026-11.py` (merged with PR 1) runs in dry-run by default and prints the plan.
   - With `--apply` and actor `jason` (source `reconcile`, request ids fixed so a re-run is a no-op) it:
     - ends crop 9 pepper (EAST-HYDRO-41), crop 14 Cannabis and crop 15 Lime Tree with `end_reason='reconciled'`, at 2026-11-05, note "Jason 2026-11-05: not in the greenhouse; only the hanging Vandas are planted";
     - for crop 5 Vanda Orchids, sets `count` to Jason's number and opens occupancy on the hanging spots he confirms (D3);
     - prints the activity ids.
   - Run it in the cluster as a one-shot Job, or from the Mac against the operator API; it is a few rows either way.
   - Verify: `SELECT count(*) FROM crops WHERE is_active` = 1; `v_position_current` shows the Vandas on the hang spots and 60 empty hydro cells.
5. **PR 4, lab site (#1329)**:
   - `render-zone-pages.py` and `render-crop-profiles.py` read the layout, occupancy and water lines.
   - The hand-written "Active Control" table on `/greenhouse/crops` is replaced by a generated occupancy table.
   - Retire `scripts/import-vault-topology.py` and `scripts/generate-hydro-map.py`; the DB is the source.
   - After the next publisher run, the lab east page matches `GET /v1/crops/locations`.

## Decisions Jason must make

| # | Decision | Recommended default |
|---|---|---|
| D1 | Hydro numbering. Is cell 1 at the left or the right as seen from the walkway? Are 1–30 the top row across all three rails, and which rail is A? | Top row is 1–30, left to right; rails A/B/C are columns 1–10, 11–20 and 21–30; bottom row 31–60 lies under them. A correction is one data update. |
| D2 | South shelf bays: which bay numbers are SE and which SW? | 1–2 are SE, 3–4 are SW. |
| D3 | Hanging Vandas: how many plants, and how many hanging spots exist? | Keep 2 hooks (CENTER-HANG-1 and -2) and record one planting "Vanda orchids, Mixed Vanda" across both, with the plant count you give. Add hooks if there are more. |
| D4 | Does wall drip L01 also reach the south and center floor pots, or only the wall shelves? | Wall shelves only. Floor pots show the misters of their zone. |
| D5 | End reason for the stale rows (pepper, cannabis, lime). | `reconciled`, dated 2026-11-05, so they are not counted as harvests or failures. |
| D6 | Keep `stage` on plantings? | Keep it optional and hide it on the map. Days in place is the main label. |
| D7 | Variety vocabulary. | Catalog crop plus free-text variety, with recently used varieties offered. No variety table yet. |
| D8 | Should a write wait for a preview? | Dashboard: the preview is in the dialog before Save. MCP: write directly, return the resolved cells, and use `dry_run` when unsure. |

## Open risks

- **Migration attestation**: migration 255 seals the `crops` trigger definitions into the login digests. The design adds no trigger, but it adds columns to `crops`. Check whether the digest covers columns; if it does, advance the receipts in the same migration, or the migrate hook fails closed.
- **Grading shift**: ending the pepper moves east grading to the default band on reconciliation day, and Statistics will show a step. Say so in the PR and the Activity note.
- **Vision**: until the vision repoint ships, any active row is offered to the model as a plant to score. Reconcile as soon as PR 1 is live, or end the stale rows earlier (see the live findings).
- **Hydro probe**: frozen values (EC with only 2 distinct values in 210 days, no water temperature since 2026-09-08) must not appear on Crops as plant health. #1183 owns the reader.
- **Layout assumptions** (D1, D2) are data. A wrong default only mislabels the map until a one-row-per-cell update.
- **In-flight W5 migrations** compete for the next number. Rebase and renumber before merging, following the per-file `SUCCESSOR_NNN` registration (Q-08).
