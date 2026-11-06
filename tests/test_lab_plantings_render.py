"""#1329: the lab crops hub, crop pages and zone pages list plantings from the active crop records.

The records are the ones the operator read model keeps (crops.is_active plus the anchor zone and position that
verdify_services.crops keeps in step with crop_occupancy), so the lab matches /v1/crops/locations. The hand-written
"Active Control" lists and zone status cards are converted once into generated blocks.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    if "asyncpg" not in sys.modules and importlib.util.find_spec("asyncpg") is None:
        stub = types.ModuleType("asyncpg")
        stub.Connection = object
        stub.connect = None
        sys.modules["asyncpg"] = stub
    spec = importlib.util.spec_from_file_location(f"lab_{name.replace('-', '_')}", ROOT / "scripts" / name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


crops = load("render-crop-profiles.py")
zones = load("render-zone-pages.py")

VANDA = {
    "planting_id": 5,
    "crop_name": "Vanda Orchids",
    "crop_variety": "Mixed Vanda",
    "crop_count": None,
    "position_label": "CENTER-HANG",
    "zone_slug": "center",
    "zone_name": "Center",
    "crop_stage": "vegetative",
    "crop_planted_date": date(2026, 4, 2),
    "crop_days_in_place": 218,
    "crop_catalog_slug": "orchid",
}
CATALOG = {
    "orchid": {"slug": "orchid", "common_name": "Vanda Orchids", "category": "flower", "season": "year-round"},
    "lettuce": {"slug": "lettuce", "common_name": "Lettuce", "category": "leafy", "season": "cool"},
    "strawberries": {"slug": "strawberries", "common_name": "Strawberries", "category": "fruit", "season": "cool"},
}

HUB = """---
title: "Crops and Growing System"
---

# Crops and Growing

Intro prose stays.

## Current Planting

- East zone: 60-position NFT hydroponics for strawberries and leafy greens.
- Center zone: hanging Vanda orchids in the airflow/misting corridor.

The structured crop records are still catching up.

## Zone-Crop Fit

Fit prose stays.

## Profile Status

<div class="metric-grid">
  <div class="metric-card"><strong>Active Control</strong><p>Currently planted.</p></div>
</div>

## Active Control

Current count: **5 active-control crop profiles**.

| [Lettuce](lettuce) | East | Hydro |

## Observed/Reference

| [Herbs](herbs) | Mixed reference |

## Planned/Retired

| [Basil](basil) | East/West |

## How Crop Profiles Drive Control

The generated Target Bands and Crop-Control Status blocks on each crop page show coverage.
"""

EAST = """# East Zone

The cool corridor.

<div class="metric-grid">
  <div class="metric-card"><strong>Active Control Zone</strong><p>Counts toward current climate control with 3 active-control crop profiles.</p></div>
  <div class="metric-card"><strong>Current Control Crops</strong><p>Lettuce, pepper, and strawberry records are occupied in <code>v_position_current</code>.</p></div>
  <div class="metric-card"><strong>Control Role</strong><p>East has the tightest envelope.</p></div>
</div>

## Current Plantings

<div class="auto-render-marker" data-auto-render="start current-plantings"></div>
old
<div class="auto-render-marker" data-auto-render="end current-plantings"></div>
"""


def test_the_planting_query_reads_only_what_the_lab_login_can_and_follows_active_records():
    sql = crops.ACTIVE_PLANTINGS_SQL
    assert "FROM crops c" in sql and "c.is_active" in sql
    # The lab login reads crops/crop_catalog/zones projections; it cannot read occupancy or the cell view.
    assert "v_position_current" not in sql and "crop_occupancy" not in sql
    assert "America/Denver" in sql
    assert not hasattr(crops, "ACTIVE_CONTROL_SLUGS") and not hasattr(crops, "CROP_TAXONOMY")


def test_the_hub_is_converted_once_and_lists_what_is_planted():
    first = crops.render_hub(HUB, [VANDA], CATALOG)
    # The hand-written plantings and the stale active-control tables are gone.
    assert "60-position NFT hydroponics" not in first
    assert "5 active-control crop profiles" not in first
    for heading in ("## Profile Status", "## Active Control", "## Observed/Reference", "## Planned/Retired"):
        assert heading not in first
    # Everything else stays where it was.
    assert first.index("Intro prose stays.") < first.index("## Current Planting") < first.index("## Zone-Crop Fit")
    assert first.index("## Zone-Crop Fit") < first.index("## Crop Profiles") < first.index("## How Crop Profiles")
    assert "Target Bands and Planting Status blocks" in first
    planted = first.split('data-auto-render="start planted-now"')[1].split('data-auto-render="end planted-now"')[0]
    assert "<strong>1</strong> active planting" in planted
    assert '<a href="/greenhouse/crops/orchid/">Vanda Orchids (Mixed Vanda)</a>' in planted
    assert "Center · CENTER-HANG" in planted
    profiles = first.split('data-auto-render="start crop-profiles"')[1].split('data-auto-render="end crop-profiles"')[0]
    assert "<strong>1</strong> planted now" in profiles
    assert profiles.index("Vanda Orchids") < profiles.index("Lettuce")  # planted first
    assert "Planted now: Center · CENTER-HANG" in profiles and "Not planted now" in profiles
    # A second run only refreshes the blocks.
    assert crops.render_hub(first, [VANDA], CATALOG) == first
    emptied = crops.render_hub(first, [], CATALOG)
    assert "Nothing planted" in emptied and "<strong>0</strong> planted now" in emptied
    assert "Intro prose stays." in emptied


def test_a_crop_page_status_follows_the_records():
    planted = crops._render_taxonomy_status("orchid", [VANDA])
    assert "<strong>Planted now</strong>" in planted
    assert "Vanda Orchids (Mixed Vanda) at Center · CENTER-HANG" in planted
    assert "v_position_current" not in planted
    idle = crops._render_taxonomy_status("lettuce", [])
    assert "<strong>Not planted now</strong>" in idle and "Active Control" not in idle
    rows = crops._render_current_plantings([VANDA], "Vanda orchids")
    assert "<strong>Center · CENTER-HANG</strong>" in rows and "Planted 2026-04-02; 218 days in place." in rows
    zone_level = crops._render_current_plantings([{**VANDA, "position_label": None}], "Vanda orchids")
    assert "<strong>Center</strong>" in zone_level


def test_a_zone_page_replaces_its_hand_written_status_cards_once():
    planting = {
        "position_label": "CENTER-HANG",
        "crop_name": "Vanda Orchids",
        "crop_variety": "Mixed Vanda",
        "is_occupied": True,
    }
    blocks = {"zone-status": zones._render_zone_status([], "East"), "current-plantings": "new"}
    content, _replaced, missing = zones._replace_auto_blocks(EAST, blocks)
    assert missing == ["zone-status"]
    seeded = zones._seed_zone_status(content, blocks["zone-status"])
    assert "Active Control Zone" not in seeded and "Lettuce, pepper, and strawberry" not in seeded
    assert "<strong>Control Role</strong>" in seeded  # hand-written context the block does not own stays
    assert seeded.index('data-auto-render="start zone-status"') < seeded.index("<strong>Control Role</strong>")
    assert "<strong>Nothing planted</strong><p>No active plantings in East right now." in seeded
    # Later runs replace the block in place.
    center = {"zone-status": zones._render_zone_status([planting], "Center"), "current-plantings": "new"}
    again, _replaced, missing = zones._replace_auto_blocks(seeded, center)
    assert missing == []
    assert "1 active planting: Vanda Orchids (Mixed Vanda) at <code>CENTER-HANG</code>." in again
    assert zones._replace_auto_blocks(again, center)[0] == again
