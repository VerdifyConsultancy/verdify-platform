#!/usr/bin/env python3
"""Refresh crawler-friendly evidence snapshots in public vault pages."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from verdify_public.output_policy import redact_non_public_crop_references  # noqa: E402

DEFAULT_API_URL = "https://api.verdify.ai/api/v1/public/evidence-snapshot"
DEFAULT_VAULT = Path("/mnt/iris/verdify-vault/website")
LOCAL_TZ = ZoneInfo("America/Denver")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def local_dt(value: str | None) -> datetime | None:
    parsed = parse_iso(value)
    return parsed.astimezone(LOCAL_TZ) if parsed else None


def fmt_local(value: str | None) -> str:
    dt = local_dt(value)
    if not dt:
        return "unknown time"
    return dt.strftime("%Y-%m-%d %H:%M %Z")


def fmt_snapshot_time(data: dict) -> str:
    return fmt_local(data.get("generated_at"))


def fmt_score_date(data: dict) -> str:
    dt = local_dt(data.get("generated_at"))
    return dt.strftime("%Y-%m-%d") if dt else datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def fmt_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "No data"
    seconds = max(0, int(round(float(seconds))))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{round(seconds / 60):.0f}m"
    return f"{seconds / 3600:.1f}h"


def fmt_number(value: int | float | None, decimals: int = 1, suffix: str = "") -> str:
    if value is None:
        return "No data"
    return f"{float(value):.{decimals}f}{suffix}"


def fmt_gallons(value: int | float | None) -> str:
    if value is None:
        return "No data"
    value = float(value)
    if value.is_integer():
        return f"{int(value)} gal"
    return f"{value:.1f} gal"


def fmt_cost(value: int | float | None) -> str:
    if value is None:
        return "No data"
    return f"USD {float(value):.2f}"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def plan_day(plan_id: str | None) -> str:
    if not plan_id:
        return "No data"
    match = re.search(r"(\d{8})", plan_id)
    if not match:
        return plan_id
    dt = datetime.strptime(match.group(1), "%Y%m%d")
    return f"{dt.strftime('%b')} {dt.day}"


def fetch_snapshot(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Verdify-Site-Publisher/1.0 (+https://lab.verdify.ai)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def planning_block(data: dict) -> str:
    pq = data.get("planning_quality") or {}
    score_date = fmt_score_date(data)
    last_validated = pq.get("last_validated_plan") or {}
    last_plan = pq.get("last_plan") or {}
    latest_lesson = pq.get("latest_lesson") or {}
    stress = pq.get("stress_breakdown") or {}

    latest_lesson_span = "No validated lesson in snapshot"
    latest_lesson_text = "No latest lesson was present in the public evidence snapshot."
    if latest_lesson:
        lesson_id = latest_lesson.get("id")
        category = redact_non_public_crop_references(latest_lesson.get("category") or "uncategorized")
        confidence = latest_lesson.get("confidence") or "unknown confidence"
        times_validated = latest_lesson.get("times_validated")
        validated = f"{times_validated}x" if times_validated is not None else "unknown"
        latest_lesson_span = f"#{lesson_id} · {category} · {confidence} confidence · validated {validated}"
        latest_lesson_text = redact_non_public_crop_references(latest_lesson.get("lesson") or latest_lesson_text)

    last_plan_id = last_plan.get("plan_id") or data.get("active_plan_id") or "No active plan"
    last_plan_status = last_plan.get("status") or data.get("active_plan_status") or "unknown status"
    last_plan_created = fmt_local(last_plan.get("created_at"))
    last_plan_age = fmt_duration(last_plan.get("age_s") or data.get("active_plan_age_s"))

    last_validated_plan_id = last_validated.get("plan_id") or data.get("last_validated_plan_id") or "No validated plan"
    validated_at = fmt_local(last_validated.get("validated_at"))
    outcome_score = last_validated.get("outcome_score")
    outcome_text = f" with outcome score {outcome_score}" if outcome_score is not None else ""

    # Graded, controller-attributable compliance (band-compliance §6-§7) is only
    # surfaced once the graded engine is promoted (migration 146/147). Until the
    # public API returns a value, the card is omitted rather than shown as "No data".
    graded_attributable = pq.get("graded_compliance_attributable_pct")
    graded_card = (
        f'\n  <div class="metric-card"><strong>{esc(fmt_number(graded_attributable, 1, "%"))}</strong>'
        "<p>Graded compliance (controller-attributable)</p></div>"
        if graded_attributable is not None
        else ""
    )

    return f"""Static public API snapshot: **{fmt_snapshot_time(data)}**. Source: [evidence snapshot JSON](https://api.verdify.ai/api/v1/public/evidence-snapshot) and [scorecard API](https://api.verdify.ai/api/v1/scorecard?date={score_date}). These values are crawler-friendly receipts; use the live dashboard link above for continuously refreshed panels.

<div class="metric-grid">
  <div class="metric-card"><strong>{esc(fmt_number(pq.get("planner_score_today"), 1))}</strong><p>Today's planner score</p></div>
  <div class="metric-card"><strong>{esc(fmt_number(pq.get("both_axis_compliance_pct"), 1, "%"))}</strong><p>Both-axis compliance (binary, house)</p></div>{graded_card}
  <div class="metric-card"><strong>{esc(fmt_number(pq.get("temp_compliance_pct"), 1, "%"))}</strong><p>Temperature compliance</p></div>
  <div class="metric-card"><strong>{esc(fmt_number(pq.get("vpd_compliance_pct"), 1, "%"))}</strong><p>VPD compliance</p></div>
  <div class="metric-card"><strong>{esc(fmt_number(pq.get("stress_axis_hours"), 2, "h"))}</strong><p>Stress-axis hours</p></div>
  <div class="metric-card"><strong>{esc(plan_day(last_validated_plan_id))}</strong><p>Last validated plan</p></div>
</div>

<div class="data-table">
  <div class="data-row"><strong>Last validated plan</strong><span>{esc(last_validated_plan_id)}</span><p>Validated {esc(validated_at)}{esc(outcome_text)}.</p></div>
  <div class="data-row"><strong>Latest plan status</strong><span>{esc(last_plan_id)} · {esc(last_plan_status)}</span><p>Written {esc(last_plan_created)}; age {esc(last_plan_age)} at snapshot time.</p></div>
  <div class="data-row"><strong>Stress breakdown</strong><span>heat {esc(fmt_number(stress.get("heat_h"), 2, "h"))} · cold {esc(fmt_number(stress.get("cold_h"), 2, "h"))} · VPD-high {esc(fmt_number(stress.get("vpd_high_h"), 2, "h"))} · VPD-low {esc(fmt_number(stress.get("vpd_low_h"), 2, "h"))}</span><p>Total stress-axis hours can exceed wall-clock hours because temperature and VPD are independent stress axes.</p></div>
  <div class="data-row"><strong>Latest lesson</strong><span>{esc(latest_lesson_span)}</span><p>{esc(latest_lesson_text)}</p></div>
</div>"""


def operations_block(data: dict) -> str:
    ops = data.get("operations") or {}
    score_date = fmt_score_date(data)
    relays = ops.get("active_relays") or data.get("active_relays") or []
    relay_span = ", ".join(map(str, relays)) if relays else "none at snapshot time"
    active_plan_id = ops.get("active_plan_id") or data.get("active_plan_id") or "No active plan"
    active_plan_status = ops.get("active_plan_status") or data.get("active_plan_status") or "unknown status"
    plan_age = fmt_duration(ops.get("active_plan_age_s") or ops.get("last_plan_age_s"))

    last_plan = (data.get("planning_quality") or {}).get("last_plan") or {}
    active_plan_created = fmt_local(last_plan.get("created_at"))
    if active_plan_created == "unknown time":
        active_plan_created = "unknown write time"

    water_today = fmt_gallons(
        ops.get("water_today_gal") if ops.get("water_today_gal") is not None else data.get("water_today_gal")
    )
    mister_water = fmt_gallons(ops.get("mister_water_today_gal"))
    water_status = ops.get("water_accounting_status") or "unknown"
    if ops.get("water_accounting_incomplete"):
        water_note = (
            f"Water accounting status is {water_status}; unattributed water remains an instrumentation limitation."
        )
    else:
        water_note = f"Water accounting status is {water_status}; this snapshot does not flag incomplete accounting."

    return f"""Static public API snapshot: **{fmt_snapshot_time(data)}**. Source: [evidence snapshot JSON](https://api.verdify.ai/api/v1/public/evidence-snapshot), [home metrics API](https://api.verdify.ai/api/v1/public/home-metrics), and [data health API](https://api.verdify.ai/api/v1/public/data-health). These values are public receipts for crawlers and locked-down browsers; use the live Operations dashboard above for continuously refreshed state.

<div class="metric-grid">
  <div class="metric-card"><strong>{esc(ops.get("data_health_status") or data.get("data_health_status") or "unknown")}</strong><p>Data-health status</p></div>
  <div class="metric-card"><strong>{esc(fmt_duration(ops.get("latest_climate_age_s") or data.get("climate_age_seconds")))}</strong><p>Latest climate age</p></div>
  <div class="metric-card"><strong>{esc(ops.get("open_critical_high_alerts") if ops.get("open_critical_high_alerts") is not None else data.get("open_critical_high_alerts"))}</strong><p>Open critical/high alerts</p></div>
  <div class="metric-card"><strong>{esc(ops.get("active_controller_mode") or data.get("controller_mode") or "unknown")}</strong><p>Active controller mode</p></div>
  <div class="metric-card"><strong>{esc(plan_age)}</strong><p>Last plan age</p></div>
  <div class="metric-card"><strong>{esc(fmt_cost(ops.get("cost_today_usd") if ops.get("cost_today_usd") is not None else data.get("cost_today_usd")))}</strong><p>Cost today</p></div>
</div>

<div class="data-table">
  <div class="data-row"><strong>Active relays</strong><span>{esc(relay_span)}</span><p>Relay state is a point-in-time snapshot of physical outputs only; Grafana below shows the full transition history.</p></div>
  <div class="data-row"><strong>Active plan</strong><span>{esc(active_plan_id)} · {esc(active_plan_status)}</span><p>Written {esc(active_plan_created)}; age {esc(plan_age)} at snapshot time. The ESP32 continues enforcing bounded setpoints while the plan awaits scorecard validation.</p></div>
  <div class="data-row"><strong>Panic check</strong><span>{esc(ops.get("open_critical_high_alerts") if ops.get("open_critical_high_alerts") is not None else data.get("open_critical_high_alerts"))} open critical/high alerts</span><p>Public panic condition is clear at snapshot time; firmware reset and component-health panels below remain the live diagnostic path.</p></div>
  <div class="data-row"><strong>Water status</strong><span>{esc(water_today)} today · {esc(mister_water)} mister water</span><p>{esc(water_note)}</p></div>
</div>"""


def replace_generated_block(text: str, kind: str, block: str) -> str:
    marker_re = re.compile(
        rf"<!-- evidence-snapshot:start {re.escape(kind)} -->.*?<!-- evidence-snapshot:end {re.escape(kind)} -->",
        re.DOTALL,
    )
    if marker_re.search(text):
        return marker_re.sub(block, text, count=1)

    legacy_re = re.compile(
        r"Static public API snapshot: \*\*.*?</div>\n\n(?=<div class=\"pg s6\">)",
        re.DOTALL,
    )
    if legacy_re.search(text):
        return legacy_re.sub(block + "\n\n", text, count=1)

    raise ValueError(f"could not find evidence snapshot block for {kind}")


def update_page(path: Path, kind: str, block: str) -> bool:
    text = path.read_text(encoding="utf-8")
    updated = replace_generated_block(text, kind, block)
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--json-file", type=Path, help="Use a captured evidence snapshot instead of fetching the API.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.json_file:
        data = json.loads(args.json_file.read_text(encoding="utf-8"))
    else:
        data = fetch_snapshot(args.api_url)

    updates = [
        (args.vault_root / "data" / "planning-quality.md", "planning-quality", planning_block(data)),
        (args.vault_root / "data" / "operations.md", "operations", operations_block(data)),
    ]

    changed: list[Path] = []
    for path, kind, block in updates:
        if not path.exists():
            print(f"skipping missing target page: {path}")
            continue
        if args.dry_run:
            replace_generated_block(path.read_text(encoding="utf-8"), kind, block)
            continue
        if update_page(path, kind, block):
            changed.append(path)

    for path in changed:
        print(path)
    if not changed:
        print("evidence snapshots already current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
